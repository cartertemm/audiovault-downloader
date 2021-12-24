import sys
import cgi
import io
import os
import time
import datetime
import getpass

# py 3 compat
try:
	from urllib.parse import urljoin
except (ModuleNotFoundError, ImportError):
	from urlparse import urljoin

import bs4
import progressbar
import requests


url = "https://audiovault.net"
TIME_FORMAT = "%Y-%m-%d"
loggedin = False
session = requests.session()


# taken and partially modified from http://code.activestate.com/recipes/578019
def bytes2human(n):
	symbols = ('KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
	prefix = {}
	for i, s in enumerate(symbols):
		prefix[s] = 1 << (i + 1) * 10
	for s in reversed(symbols):
		if n >= prefix[s]:
			value = float(n) / prefix[s]
			return '%.1f%s' % (value, s)
	return "%sB" % n


def menu(prompt, items):
	"""Constructs and shows a simple commandline menu.
	Returns the user input."""
	for i in range(len(items)):
		print(str(i+1) + ": " + items[i])
	result = None
	while True:
		result = input(prompt)
		if result and result.isdigit():
			result = int(result)
			break
	if result == 0:
		return
	return result-1


def authenticate():
	global loggedin
	while True:
		email = input("Enter email: ")
		password = getpass.getpass("Enter password: ")
		try:
			l = login(email, password)
		except:
			print("An unknown error occurred while trying to log you in. Please report this to the developer")
			raise
		if l:
			loggedin = True
			print("Login successful")
			return True
		else:
			print("The email or password was incorrect.")
			if input("Would you like to try again?").lower().startswith("y"):
				continue
			return False


def download(url, destination=None, callback=None, progress_bar=False, requests_session=None, head_verifier=None):
	"""Downloads a file to disk in chunks, optionally reporting progress information through a callback, progress bar or both.
	If destination is given, the download will be saved there. Otherwise we parse the filename attribute of the content-disposition header.
		You can also provide a directory to join with the file.
	If callback is given, it will be called with one parameter, the percentage of the transfer.
	"""
	buffer_size = io.DEFAULT_BUFFER_SIZE
	if callback and not callable(callback):
		callback = None
	if not requests_session:
		requests_session = requests.session()
	r = requests_session.get(url, stream=True)
	if head_verifier and callable(head_verifier):
		v = head_verifier(r)
		if not v:
			return False
	total_size = int(r.headers.get("Content-Length", 0))
	print("Size: "+bytes2human(total_size))
	if total_size == 0:
		return False
	so_far = 0
	_, params = cgi.parse_header(r.headers.get("content-disposition", ""))
	if not destination:
		destination = params.get("filename")
	elif os.path.isdir(destination):
		destination = os.path.join(destination, params.get("filename", ""))
	if not destination or destination[-1] == os.path.sep:
		return False  # tried so hard, but it just wasn't meant to be. :(
	with open(destination, "wb") as f:
		if progress_bar:
			bar = progressbar.ProgressBar().start()
		for chunk in r.iter_content(buffer_size):
			f.write(chunk)
			so_far += len(chunk)
			percent = round((so_far/total_size)*100, 2)
			if callback:
				callback(percent)
			if progress_bar:
				bar.update(percent)
		if progress_bar:
			bar.finish()
	return True


def find_latest_csvs():
	def _max(lst):
		# in case of a generator
		lst = [i for i in lst]
		return None if len(lst) == 0 else max(lst)
	# list of [name, datetime object]
	movies = []
	shows = []
	for file in os.listdir("."):
		if os.path.splitext(file)[1] == ".csv":
			t = file[file.rfind("_")+1:-4]
			dt = datetime.datetime.strptime(TIME_FORMAT, t)  # timezone unaware
			if file.startswith("movies_"):
				movies.append([t, dt])
			elif file.startswith("shows_"):
				shows.append([t, dt])
	# get the youngest CSV for each category
	now = datetime.datetime.now()
	m = _max(dt for (name, dt) in movies if dt < now)
	s = _max(dt for (name, dt) in shows if dt < now)
	return [
		movies.index(m) if m else None,
		shows.index(s) if s else None
	]


def search(query, kind="movies"):
	r = session.get(urljoin(url, kind), params={"search": query})
	r.raise_for_status()
	return parse_pages(r.text)


def login(email, password):
	# first, we need to retrieve the token from the login page
	r = session.get(urljoin(url, "login"))
	r.raise_for_status()
	soup = bs4.BeautifulSoup(r.text, features="html.parser")
	token = soup.find("input", {"type": "hidden", "name": "_token"})
	if not token:
		print("Unable to retrieve login token")
		return
	token = token.get("value")
	# now we can actually send the request
	r = session.post(urljoin(url, "login"), {
		"_token": token,
		"email": email,
		"password": password,
	})
	# todo: We need a more reliable way of verifying logins.
	if r.text.startswith("<form method=\"POST\" action="):
		return False
	return True


def get_recents(kind):
	r = session.get(url)
	r.raise_for_status()
	soup = bs4.BeautifulSoup(r.text, features="html.parser")
	t = None
	for tag in soup.find_all("h5"):
		text = str(tag.text.strip())
		if text.startswith("Recent") and kind.capitalize() in text:
			t = tag
			break
	if not t:
		print("error locating a tag for recent "+kind)
		return
	table = t.find_next("tbody")
	return parse_page(table)


def parse_pages(text):
	if isinstance(text, bs4.BeautifulSoup):
		soup = text
	else:
		soup = bs4.BeautifulSoup(text, features="html.parser")
	# to get to the last page, we try finding next and walk back from there
	next = soup.find("a", rel="next")
	if next:
		last = next.findPrevious("a", {"class": "page-link"})
		last = last.get("href")
		last_url = last
		last = last[last.rfind("=")+1:]
		last = int(last)
		print(last, last_url)
		final = []
		for i in range(1, last+1):
			print("Sleeping...")
			time.sleep(random.randint(0.4, 5))
			r = session.get(last_url)
			r.raise_for_status()
			print(f"parsing page {i}")
			soup = bs4.BeautifulSoup(r.text, features="html.parser")
			table = soup.find("tbody")
			final+= parse_page(table)
	else:  # this is the only page
		table = soup.find("tbody")
		final = parse_page(table)
	return final


def parse_page(table):
	res = []
	for row in table.findAll("tr"):
		id = row.findNext("td")
		name = id.findNext("td")
		link = name.findNext("td").find("a").get("href")
		if id:
			id = id.text.strip()
		if name:
			name = name.text.strip()
		res.append([id, name, link])
	return res


def head_verifier(r):
	if r.status_code == 302 or r.headers.get("Content-Type", "").startswith("text/html"):
		print("The session timed out. Logging you in again...")
		authenticate()
		return False
	return True


def main():
	s = None
	items = (
		"Find a movie",
		"Find a TV show",
		"View recently added movies",
		"View recently added shows",
		"Exit"
	)
	m = menu("What would you like to do? ", items)
	if m == None:
		return
	if m < 2:
		q = input("Search for what? ")
		if m == 0:
			kind = "movies"
		else:
			kind = "shows"
		s = search(q, kind=kind)
	elif m < 4:
		if m == 2:
			kind = "movies"
		else:
			kind = "shows"
		s = get_recents(kind)
	elif m == 4:
		sys.exit(0)
	if s and len(s) > 0:
		print("Listing "+kind)
		items = [f"{name} ({id})" for (id, name, link) in s]
		m = menu("Which would you like to download? ", items)
		if m == None:
			return
		if not loggedin:
			authenticate()
		print(f"Downloading {s[0]}")
		download(s[m][2], progress_bar=True, requests_session=session, head_verifier=head_verifier)
	else:
		print("Nothing found")


if __name__ == "__main__":
	while True:
		main()
