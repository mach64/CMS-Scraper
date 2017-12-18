import requests
from bs4 import BeautifulSoup
import json
import os
import shutil
import configparser  # to access the configuration file
from pathlib import Path
import zipfile
import rarfile

sess = requests.Session()  # session to store cookies and remain logged in
moodle_url = 'http://id.bits-hyderabad.ac.in/moodle/'


def get_config(path):
	"""Load the config using configparser."""
	if not os.path.exists(path):
		print(f'{path} not found!')
		exit(0)
	config = configparser.ConfigParser()
	config.read(path)
	return config


def read_file(path, no_data, data_handler):
	"""Properly handle reading a file."""
	if not os.path.isfile(path):
		print(f'{path} not found!')
		return no_data()
	with open(path, 'r') as f:
		data = f.read()
		if not data:  # in case the file is empty
			print(f'{path} is empty.')
			return no_data()
		return data_handler(data)


def get_attr(text, param, offset=0, end_ch='"'):
	"""Function to extract substring from a text."""
	x = text.find(param) + offset  # left index of the substring
	if x == offset - 1:
		raise EOFError  # in case the parameter is not found
	y = text[x:].find(end_ch)  # right index of the substring
	if y == -1:
		substring = text[x:]
	else:
		substring = text[x:][:y]
	return substring


def make_fold(parent, name):
	"""Create a directory from Path object."""
	for ch in ['\\', '/']:
		name = name.replace(ch, ' ')
	folder = parent / name
	if not folder.is_dir():
		folder.mkdir()
		print(f'Created {folder!s}.')
	return folder


def login(user=None, pwd=None):
	"""Login to CMS."""
	if not user:
		user = config['CREDS']['username']
		pwd = config['CREDS']['password']
	url = moodle_url + 'login/index.php'
	payload = {'username': user, 'password': pwd, 'Submit': 'Login'}
	r = sess.post(url, data=payload)
	if r.text.lower().find('invalid login') != -1:  # word only appears when login unsuccessful
		print("Incorrect username/password.")
	elif r.text.lower().find('dashboard') != -1:  # word only appears when login is successful
		print("Login successful!")
	else:
		print("Error")


def get_all_courses():
	"""Scrape CMS and return list of IDS of courses."""
	ids = []
	url = moodle_url + 'course/index.php?categoryid=5&browse=courses&perpage=5'
	print('Getting course IDS of all courses.', end=' ')
	for i in range(0, 168):
		r = sess.get(url, params={'page': str(i)})
		soup = BeautifulSoup(r.text, 'html.parser')
		courses = soup.find_all('div', {'class': 'coursename'})
		for course in courses:
			link = course.a['href']
			c_id = get_attr(link, '=', 1)
			ids.append(c_id + '\n')

	with open('all_ids.txt', 'w') as f:  # store in file to avoid having to scrape everytime
		f.writelines(ids)
	print('Done.')
	return ids


def get_enrol_payload(c, c_id):
	"""Create POST data for Enrollment."""
	inst = get_attr(c.text, 'instance', 31)
	sesskey = get_attr(c.text, 'sesskey', 10)
	self_enrol = '_qf__{}_enrol_self_enrol_form'.format(inst)
	isexp = 'mform_isexpanded_id_selfheader'
	payload = {
		'id': c_id,
		'instance': inst,
		'sesskey': sesskey,
		self_enrol: 1,
		isexp: 1,
		'submitbutton': 'Enrol me'
	}
	return payload


def course_enrol(c_id):
	"""Enrol into a course."""
	c_url = moodle_url + 'course/view.php'
	form_url = moodle_url + 'enrol/index.php'
	c = sess.get(c_url, params={'id': c_id})
	if c.text[77:84] == 'Course:':
		print('Already enrolled to', c_id)
		return 1
	payload = get_enrol_payload(c, c_id)
	code = sess.post(form_url, data=payload)
	if code.text.lower().find('course:') is -1:
		print('Enrollment unsuccessful for', c_id)
		return -1
	else:
		print('Enrolled to', c_id)
		return 0


def course_unenrol(c_id):
	"""Unenrol from a course."""
	print("Unenrolling from", c_id, end='. ')
	course_url = moodle_url + 'course/view.php'
	c = sess.get(course_url, params={'id': c_id})
	if c.text[77:84] != 'Course:':
		print('Not enrolled to', c_id)
		return
	enrolid = get_attr(c.text, 'enrolid', 8)
	sesskey = get_attr(c.text, 'sesskey', 10)
	unenrol_url = moodle_url + 'enrol/self/unenrolself.php'
	payload = {
		'enrolid': enrolid,
		'confirm': '1',
		'sesskey': sesskey
	}
	sess.post(unenrol_url, data=payload)
	print('Done.')


def fold_contents(fold_url):
	"""Browse a folder and get the download links."""
	f = sess.get(fold_url)
	soup = BeautifulSoup(f.text, 'html.parser')
	contents = get_folders(soup)  # look for sub-directories
	files = soup.find_all('span', {'class': 'fp-filename-icon'})
	for file in files:
		link = file.find('a')  # find the link tag
		file_name = link.find('span', {'class': 'fp-filename'}).text
		file_data = {
			'name': file_name,
			'type': 'file',
			'downloaded': False,
			'url': link['href']
		}
		contents.append(file_data)
	return contents


def get_folders(src, existing_contents=[]):
	"""Get all folders, with their contents, from a course/folder."""
	folders = src.find_all('li', {'class': 'activity folder modtype_folder '})
	folds = []
	for fold in folders:
		link = fold.find('a')
		fold_name = link.span.find(text=True, recursive=False)
		fold_url = link['href']
		fold_id = int(get_attr(fold_url, 'id=', 3))
		if fold_id in existing_contents:  # skip the folder if already in db
			continue
		contents = fold_contents(fold_url)
		folder_data = {
			'name': fold_name,
			'type': 'folder',
			'id': fold_id,
			'downloaded': False,
			'contents': contents
		}
		folds.append(folder_data)
	return folds


def get_files(src, existing_contents=[]):
	"""Get all the file links from a course page."""
	files = src.find_all('li', {'class': 'activity resource modtype_resource '})
	f = []
	for file in files:
		link = file.find('a')
		name = link.span.find(text=True, recursive=False)
		file_id = int(get_attr(link['href'], 'id=', 3))
		if file_id in existing_contents:  # skip the file if already in db
			continue
		file_data = {
			'name': name,
			'type': 'file',
			'id': file_id,
			'downloaded': False
		}
		f.append(file_data)
	return f


def get_course_links(c_id, course):
	"""Get link of each file for a course."""
	c_url = moodle_url + 'course/view.php'
	r = sess.get(c_url, params={'id': c_id})
	soup = BeautifulSoup(r.text, 'html.parser')

	course['name'] = soup.find('h3', {'class': 'page-subtitle'}).text
	print(f'Getting links for {course["name"]}.')

	existing_contents = [content['id'] for content in course['contents']]
	course['contents'].extend(get_folders(soup, existing_contents))  # the folders in a course are traversed here
	course['contents'].extend(get_files(soup, existing_contents))


def read_course(c_id, db):
	"""Get the course from database, else return a new course."""
	for course in db:
		if c_id == str(course['id']):
			return course

	course = {
		'name': '',
		'type': 'course',
		'id': int(c_id),
		'remain enrolled': 0,
		'contents': []
	}
	db.append(course)  # add the new course to the database
	return read_course(c_id, db)


def update_db():
	"""Get links for courses and update the courses_db."""
	print("Updating database.")
	ids = read_file('all_ids.txt', get_all_courses, lambda d: d.split('\n'))
	db = read_file('courses_db.json', lambda: [], lambda d: json.loads(d))
	for c_id in ids:  # TODO: Break up enrolment into small groups.
		remain_enrolled = course_enrol(c_id)
		if remain_enrolled is -1:  # in case enrollment was unsuccessful
			continue
		course = read_course(c_id, db)
		get_course_links(c_id, course)
		course["remain enrolled"] = remain_enrolled

		with open('courses_db.json', 'w') as f:
			f.write(json.dumps(db, indent=4))


def traverse_fold(fold_path):
	contents = []
	for fpath in fold_path.iterdir():
		content = {
			"name": str(fpath.relative_to(fold_path)),
			"type": fpath.is_file() and "file" or "folder"
		}
		if fpath.is_dir():
			content['contents'] = traverse_fold(fpath)
		contents.append(content)
	return contents


def extract_archive(file_data, archive_path):
	"""Extract the archive to a folder."""
	if archive_path.suffix == '.zip':
		archive = zipfile.ZipFile(str(archive_path), 'r')
	elif archive_path.suffix == '.rar':
		rarfile.UNRAR_TOOL = config['DEFAULT']['unrar_path']
		archive = rarfile.RarFile(str(archive_path), 'r')

	folder = make_fold(archive_path.parent, archive_path.stem)
	print('Extracting the archive.', end=' ')
	archive.extractall(str(folder))
	archive.close()
	archive_path.unlink()  # delete the archive
	print('Done.')

	contents = list(folder.iterdir())
	if len(contents) == 1 and contents[0].is_dir():  # in case the archive only contained one folder
		parent_fold = contents[0].parents[1]
		temp_fold = parent_fold / 'temp'
		contents[0].rename(temp_fold)
		contents[0].parent.rmdir()
		folder = parent_fold / contents[0].stem
		temp_fold.rename(folder)
	file_data['contents'] = traverse_fold(folder)


def download_file(file, folder):
	"""Where the actual downloading happens."""
	if 'url' in file:  # for files inside folders
		file_url = file['url']
		del file['url']
	else:  # for files directly inside course directory
		file_url = moodle_url + "mod/resource/view.php?id=" + str(file['id'])

	r = sess.get(file_url, stream=True)

	try:
		file_name = get_attr(r.headers['Content-Disposition'], '="', 2, '"')
	except KeyError:
		print('Unable to access file. Check if you are logged in and enrolled.')
		return

	print(f"Downloading {file_name}.", end=' ')
	file['real_name'] = file_name  # this is the name by which file is saved
	file_path = folder / file_name
	if file_path.exists():  # in case the file already exists
		print('Already exists.')
		return

	with open(str(file_path), 'wb') as f:
		shutil.copyfileobj(r.raw, f)
		print('Done.')

	if any([file_path.suffix == ext for ext in ['.zip', '.rar']]):
		extract_archive(file, file_path)


def download_contents(contents, fold):
	"""Download files of a course/folder."""
	for content in contents:
		if content['downloaded']:  # to avoid redownloading the file/folder
			print(f"Already downloaded {content['name']}.")
			continue
		if content['type'] == 'folder':
			new_fold = make_fold(fold, content['name'])
			download_contents(content['contents'], new_fold)  # Recursively traverse the folders in case of sub-directories.
		elif content['type'] == 'file':
			download_file(content, fold)
		content['downloaded'] = True


def download():
	"""Download the files for all courses."""
	print('Downloading files.')
	root_fold = make_fold(Path().cwd(), config['DEFAULT']['root'])
	courses = read_file('courses_db.json', update_db, lambda d: json.loads(d))

	for course in courses:
		print(f"Downloading contents of {course['name']}.")
		fold = make_fold(root_fold, course['name'])
		download_contents(course['contents'], fold)
		if not course['remain enrolled']:
			course_unenrol(course['id'])

		with open('courses_db.json', 'w') as f:
			f.write(json.dumps(courses, indent=4))


def main():
	update_db()
	download()

config = get_config('config.ini')

if __name__ == '__main__':
	login()
	# get_all_courses()  # required on first run
	main()
