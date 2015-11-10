import json
from operator import itemgetter
from collections import OrderedDict
from ..application import mongo, app, mail
from ..forms import SameDiffUpload, SameDiffSample
from ..logic import filehandler
import databasic.tasks
from flask import Blueprint, render_template, request, redirect, url_for, g, abort
from flask.ext.babel import lazy_gettext as _

mod = Blueprint('samediff', __name__, url_prefix='/<lang_code>/samediff', template_folder='../templates/samediff')


@mod.route('/', methods=('GET', 'POST'))
def index():

	forms = OrderedDict()
	forms['sample'] = SameDiffSample()
	forms['upload'] = SameDiffUpload()

	if request.method == 'POST':

		btn_value = request.form['btn']
		email = None
		is_sample_data = False

		if btn_value == 'upload':
			files = request.files.getlist('upload')
			file_paths = filehandler.open_docs(files)
			email = forms['upload'].data['email']
		elif btn_value == 'sample':
			file_paths = forms['sample'].data['samples']
			is_sample_data = True
			email = forms['sample'].data['email']

		if btn_value is not None and btn_value is not u'':
			return queue_files(file_paths, is_sample_data, email)

	return render_template('samediff/samediff.html', forms=forms.items(), tool_name='samediff')

@mod.route('/results')
def results():

	doc_id = None if not 'id' in request.args else request.args['id']
	if doc_id is None:
		return redirect(g.current_lang + '/samediff')

	job = mongo.find_document('samediff', doc_id)

	if not 'complete' in job['status']:
		return render_template('samediff/results.html', results=job, tool_name='samediff')

	# interpret cosine similarity for top part of report 
	# If there are only 2 docs then make a statement about how similar they are to each other
	if len(job['filenames']) == 2:
		cosineDiff = abs(job['cosineSimilarity'][0][0] - job['cosineSimilarity'][0][1])
		job['humanReadableSimilarity'] = interpretCosineSimilarity(cosineDiff)
	else:
		maxInfo = {'score':0}
		minInfo = {'score':1}
		for r in range(len(job['filenames'])):
			for c in range(r+1,len(job['filenames'])):
				score = job['cosineSimilarity'][r][c]
				if score >= maxInfo['score']:
					maxInfo = { 'score':score, 'doc1':r, 'doc2':c}
				if score <= minInfo['score']:
					minInfo = { 'score':score, 'doc1':r, 'doc2':c}

		job['mostSimilar'] = [ job['filenames'][maxInfo['doc1']], job['filenames'][maxInfo['doc2']] ]
		job['mostDifferent'] = [ job['filenames'][minInfo['doc1']], job['filenames'][minInfo['doc2']] ]
	#	
	# Find the lowest average cosine similarity to figure out which doc is the most unique
	#
	averages = []
	for fileCS in job['cosineSimilarity']:
		averageSimilarity = 0
		for cs in fileCS:
			averageSimilarity = averageSimilarity + cs
		averages.append( averageSimilarity / len(job['filenames']) )

	minCS = min(averages)
	mins = [i for i, j in enumerate(averages) if j == minCS]

	if mins is not None and len(mins) > 0:
		job['mostDifferentFile'] = job['filenames'][mins[0]]

	# figure out the highest TfIdf score
	allScores = []
	for docResults in job['tfidf']:
		scores = [ t['tfidf'] for t in docResults]
		allScores = allScores + scores
	maxTfIdf = max(allScores)

	# build thresholded lists of file similarity scores
	job['similarityLists'] = []
	for row in range(0,len(job['filenames'])):
		info = [ [], [], [], [], [] ]
		for col in range(0,len(job['filenames'])):
			if row==col:
				continue
			score = job['cosineSimilarity'][row][col]
			name = job['filenames'][col] + " ("+("{0:.2f}".format(score))+")"
			if score < 0.5:
				info[0].append(name)
			elif score < 0.7:
				info[1].append(name)
			elif score < 0.8:
				info[2].append(name)
			elif score < 0.9:
				info[3].append(name)
			elif score < 1.0:
				info[4].append(name)
		job['similarityLists'].append(info)

	return render_template('samediff/results.html', results=job, tool_name='samediff', maxTfIdfScore=maxTfIdf)

@mod.route('/results/<file1>-and-<file2>-common-words')
def show_common_words(file1, file2):
	doc_id = None if not 'id' in request.args else request.args['id']
	if doc_id is None:
		return redirect(g.current_lang + '/samediff')
	try:
		job = mongo.find_document('samediff', doc_id)
		results = _most_common_words(doc_id,file1,file2)
		return render_template("samediff/words-in-common.html", job=job, file1=file1, file2=file2, data=results)
	except Exception as e:
		# logger.exception(e)
		print e
		abort(400)

def _most_common_words(job_id,filename1,filename2):
	# job = app.db_collection.find_one({'_id':ObjectId(job_id)})
	job = mongo.find_document('samediff', job_id)
	doc1_idx = job['filenames'].index(filename1)
	doc2_idx = job['filenames'].index(filename2)
	# TODO: catch case where filename isn't in results
	doc1_freq_dist = { t['term']:t['frequency'] for t in job['tfidf'][doc1_idx]}
	doc2_freq_dist = { t['term']:t['frequency'] for t in job['tfidf'][doc2_idx]}
	terms = set(doc1_freq_dist.keys()+doc2_freq_dist.keys())
	# logger.debug("  Found %d words total" % (len(terms)))
	results = [ {'term':t,'avg':float(doc1_freq_dist[t]+doc2_freq_dist[t])/2.0, 
			  'doc1':doc1_freq_dist[t], 'doc2':doc2_freq_dist[t],
			  'total':doc1_freq_dist[t]+doc2_freq_dist[t]} for t in terms 
		if t in doc1_freq_dist.keys() and t in doc2_freq_dist.keys() ]
	results = sorted(results, key=itemgetter('avg','total'),reverse=True)
	# logger.debug("  Found %d common words" % (len(results)))
	return results

'''
# trying to track status of the celery task here
@mod.route('/status/<task_id>')
def taskstatus(task_id):
	task = queue_files.AsyncResult(task_id)
	response = task.state
	return json.dumps(response)
	# return redirect('/')
'''

def queue_files(file_paths, is_sample_data, email):
	file_names = filehandler.get_file_names(file_paths)
	job_id = mongo.save_queued_files('samediff', file_paths, file_names, is_sample_data, email, request.url + 'results?id=')
	result = databasic.tasks.save_tfidf_results.apply_async(args=[job_id])
	print result
	return redirect(request.url + 'results?id=' + job_id)
	# return url_for('.taskstatus', task_id=result.id), 202

def interpretCosineSimilarity(cosineDiff):
	# Cosine Similarity
	if cosineDiff <= 0.1:
		return _('similar')
	elif cosineDiff <= 0.2:
		return _('sort of similar')
	elif cosineDiff <= 0.3:
		return _('pretty different')
	else:
		return _('very different')