from ..application import app
from bs4 import BeautifulSoup as bs
from urllib2 import urlopen
import os, datetime, time, tempfile, codecs, unicodecsv, json, xlrd, logging
from pyth.plugins.rtf15.reader import Rtf15Reader
from pyth.plugins.plaintext.writer import PlaintextWriter
from flask import Response, abort
from flask.ext.uploads import UploadSet, configure_uploads, TEXT, patch_request_class, UploadNotAllowed
from docx import opendocx, getdocumenttext

ENCODING = 'utf-8'

# setup file uploading
TEMP_DIR = tempfile.gettempdir()
app.config['UPLOADED_DOCS_DEST'] = TEMP_DIR
docs = UploadSet(name='docs', extensions=('txt', 'docx', 'rtf', 'csv', 'xlsx', 'xls'))
configure_uploads(app, (docs))
patch_request_class(app, 10 * 1024 * 1024) # 10MB

basedir = os.path.dirname(os.path.abspath(__file__))
sample_data_dir_path = os.path.join(basedir,'../','../','sample-data')
logging.error("Loading sameple data from %s" % sample_data_dir_path)
sample_data_config_path = os.path.join(basedir,'../','../','config','sample-data.json')
logging.error("Loading sameple data config from %s" % sample_data_config_path)
if os.path.isdir(sample_data_dir_path) and os.path.exists(sample_data_config_path):
	samples = json.load(open(sample_data_config_path))

def write_to_temp_file(text):
	file_path = _get_temp_file()
	file = codecs.open(file_path, 'w', ENCODING)
	file.write(text)
	file.close()
	return file_path

def write_to_csv(headers, rows, file_name_suffix=None):
	file_path = _get_temp_file(file_name_suffix)
	with open(file_path, 'w') as f:
		writer = unicodecsv.writer(f, encoding=ENCODING)
		writer.writerow(headers)
		for row in rows:
			writer.writerow(row)
	return _get_file_name(file_path)

def generate_csv(file_name):
	file_path = os.path.join(TEMP_DIR, file_name)
	if not os.path.isfile(file_path):
		return abort(400)
	def generate():
		with open(file_path, 'r') as f:
			reader = unicodecsv.reader(f, encoding=ENCODING)
			for row in reader:
				yield ','.join(row) + '\n'
	return Response(generate(), headers={'Content-Disposition':'attachment;filename='+file_name},mimetype='text/csv')

def convert_to_txt(file_path):
	words = None
	ext = _get_extension(file_path)
	if ext == '.txt':
		with codecs.open(file_path, 'r', ENCODING) as myfile:
			words = myfile.read()
	elif ext == '.docx':
		words = _docx_to_txt(file_path)
	elif ext == '.rtf':
		doc = Rtf15Reader.read(open(file_path))
		words = PlaintextWriter.write(doc).getvalue()
	return words

def convert_to_csv(file_path):
	ext = _get_extension(file_path)
	if ext == '.csv':
		return [file_path]
	elif ext == '.xlsx' or ext == '.xls':
		wb = xlrd.open_workbook(file_path)
		files = []
		for i in range(wb.nsheets):
			files.append(_open_sheet(wb, i))
		return files
	print ext + ' could not be converted to csv'
	return [file_path]

def open_doc(doc):
	try:
		file_name = docs.save(doc)
		file_path = os.path.join(TEMP_DIR, file_name)
		return file_path
	except UploadNotAllowed:
		print "supported filetypes: txt, docx, rtf, csv, xlsx, xls, love"

def open_docs(docs):
	file_paths = []
	for doc in docs:
		file_paths.append(open_doc(doc))
	return file_paths

def delete_files(file_paths):
	for f in file_paths:
		delete_file(f)

def delete_file(file_path):
	os.remove(file_path)

def open_workbook(book):
	file_paths = []
	for i, worksheet in enumerate(book.worksheets()):
		file_path = _get_temp_file('-' + worksheet.title + '.csv')
		with open(file_path, 'wb') as f:
			writer = unicodecsv.writer(f, encoding=ENCODING, delimiter=str(u';'), quotechar=str(u'"'))
			writer.writerows(worksheet.get_all_values())
		file_paths.append(file_path)
	return file_paths

def get_samples(tool_id):
	choices = []
	texts = []
	for text in samples:
		if tool_id in text['modules']:
			texts.append((text['source'], text['title']))
	choices = texts
	return choices

def get_sample_title(path):
	for text in samples:
		if path in text['source']:
			return text['title']
	return path

def get_file_names(file_paths):
	file_names = []
	for f in file_paths:
		file_names.append(_get_file_name(f))
	return file_names

def generate_filename(ext, suffix, *args):
	files = '-'.join(args) + '-' if len(args) > 0 else ''
	suffix = suffix + '-' if suffix is not None and suffix is not '' else ''
	suffix = suffix.replace(' ', '-')
	ext = ext[1:] if '.' in ext[0] else ext
	return files + suffix + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + '.' + ext

def download_webpage(url):
	soup = bs(urlopen(url))
	soup.p.encode(ENCODING)
	for script in soup(['script', 'style']):
		script.extract()
	return {'title': soup.title.string, 'text': soup.get_text()}

def _open_sheet(workbook, index):
	sh = workbook.sheet_by_index(index)
	name = workbook.sheet_names()[index]
	new_file = _get_temp_file('-' + name + '.csv')
	with open(new_file, 'wb') as f:
		writer = unicodecsv.writer(f, encoding=ENCODING, delimiter=str(u';'), quotechar=str(u'"'))
		for row in xrange(sh.nrows):
			writer.writerow(sh.row_values(row))
	return new_file

def _get_file_name(file_path):
	return os.path.split(file_path)[1]

def _get_temp_file(file_name_suffix=None):
	file_name = time.strftime("%Y%m%d-%H%M%S")
	if file_name_suffix is not None:
		file_name += file_name_suffix
	return os.path.join(TEMP_DIR, file_name)

def _get_extension(file_path):
	return os.path.splitext(file_path)[1]

def _docx_to_txt(file_path):
	# http://davidmburke.com/2014/02/04/python-convert-documents-doc-docx-odt-pdf-to-plain-text-without-libreoffice/
	document = opendocx(file_path)
	paratextlist = getdocumenttext(document)
	newparatextlist = []
	for paratext in paratextlist:
		newparatextlist.append(paratext.encode(ENCODING))
	return '\n\n'.join(newparatextlist)
