#!/usr/bin/env python
# -*- coding: utf-8 -*-
# see: http://amp.pharm.mssm.edu/Enrichr/help#api for API docs

import sys, json, os, logging
import requests
import pandas as pd
from io import StringIO
from time import sleep
from tempfile import TemporaryDirectory
from gseapy.plot import barplot
from gseapy.parser import get_library_name
from gseapy.utils import *


class Enrichr(object):
    """Enrichr API"""
    def __init__(self, gene_list, gene_sets, descriptions='foo', 
                 outdir='Enrichr', cutoff=0.05, format='pdf', 
                 figsize=(6.5,6), top_term=10, no_plot=False, verbose=False):

        self.gene_list=gene_list
        self.gene_sets=gene_sets
        self.descriptions=str(descriptions)
        self.outdir=outdir
        self.cutoff=cutoff
        self.format=format
        self.figsize=figsize
        self.__top_term=int(top_term)
        self.__no_plot=no_plot
        self.verbose=bool(verbose)
        self.module="enrichr"
        self.res2d=None
        self._processes=1

        # init logger
        logfile = self.prepare_outdir()
        self._logger = log_init(outlog=logfile,
                                log_level=logging.INFO if self.verbose else logging.WARNING)

    def prepare_outdir(self):
        """create temp directory."""
        self._outdir = self.outdir
        if self._outdir is None:
            self._tmpdir = TemporaryDirectory()
            self.outdir = self._tmpdir.name
        elif isinstance(self.outdir, str):
            mkdirs(self.outdir)
        else:
            raise Exception("Error parsing outdir: %s"%type(self.outdir))

        # handle gene_sets
        logfile = os.path.join(self.outdir, "gseapy.%s.%s.log" % (self.module, self.descriptions))
        return logfile

    def parse_genesets(self):

        if isinstance(self.gene_sets, list):
            return
        elif isinstance(self.gene_sets, str):
            return self.gene_sets.split(",")
        else:
            raise Exception("Error parsing enrichr libraries, please provided corrected one")

    def parse_genelists(self):
        """parse gene list"""
        if isinstance(self.gene_list, list):
            genes = [str(gene) for gene in self.gene_list]
        elif isinstance(self.gene_list, pd.DataFrame):
            # input type is bed file
            if self.gene_list.shape[1] >=3:
                genes= self.gene_list.iloc[:,:3].apply(lambda x: "\t".join([str(i) for i in x]), axis=1).tolist()
            # input type with weight values
            elif self.gene_list.shape[1] == 2:
               genes= self.gene_list.apply(lambda x: ",".join([str(i) for i in x]), axis=1).tolist()
            else:
               genes = self.gene_list.squeeze().tolist()
        elif isinstance(self.gene_list, pd.Series):
            genes = self.gene_list.squeeze().tolist()
        else:
            # get gene lists or bed file, or gene list with weighted values.
            genes=[]
            with open(self.gene_list) as f:
                for gene in f:
                    genes.append(gene.strip())

        genes_str = '\n'.join(genes)
        return genes_str

    def send_genes(self, gene_list, url):
        """ send gene list to enrichr server"""
        payload = {
          'list': (None, gene_list),
          'description': (None, self.descriptions)
           }
        # response
        response = requests.post(url, files=payload)
        if not response.ok:
            raise Exception('Error analyzing gene list')
        sleep(1)
        job_id = json.loads(response.text)

        return job_id

    def check_genes(self, gene_list, usr_list_id):
        '''
        Compare the genes send and received to get succesfully recognized genes
        '''
        response = requests.get('http://amp.pharm.mssm.edu/Enrichr/view?userListId=%s' % usr_list_id)
        if not response.ok:
            raise Exception('Error getting gene list back')
        returnedL = json.loads(response.text)["genes"]
        returnedN = sum([1 for gene in gene_list if gene in returnedL])
        self._logger.info('{} genes successfully recognized by Enrichr'.format(returnedN))

    def get_results(self, gene_list):
        """Enrichr API"""
        ADDLIST_URL = 'http://amp.pharm.mssm.edu/Enrichr/addList'
        # RESULTS_URL = 'http://amp.pharm.mssm.edu/Enrichr/enrich'
        # query_string = '?userListId=%s&backgroundType=%s'
        job_id = self.send_genes(gene_list, ADDLIST_URL)
        user_list_id = job_id['userListId']

        RESULTS_URL = 'http://amp.pharm.mssm.edu/Enrichr/export'
        query_string = '?userListId=%s&filename=%s&backgroundType=%s'
        # set max retries num =5
        s = retry(num=5)
        filename = "%s.%s.reports" % (self._gs, self.descriptions)
        url = RESULTS_URL + query_string % (user_list_id, filename, self._gs)
        response = s.get(url, stream=True, timeout=None)
        # response = requests.get(RESULTS_URL + query_string % (user_list_id, gene_set))
        sleep(1)
        res = pd.read_table(StringIO(response.content.decode('utf-8')))
        return [job_id['shortId'], res]

    def run_v2(self):
        """run enrichr for multi library input"""
        gs = self.gene_sets.split(",")
        self.results = pd.DataFrame()
        for g in gs:
            self._gs = str(g)
            self._logger.debug("Start Enrichr using library: %s"%(self._gs))
            self.run_single()
        # clean up tmpdir
        if self._outdir is None: self._tmpdir.cleanup()
        return

    def run(self):
        """run enrichr for one sample gene list but multi-libraries"""

        # read input file
        genes_list = self.parse_genelists()
        gss = unique(self.parse_genesets())
        self._logger.info("Connecting to Enrichr Server to get latest library names")
        # gss = self.gene_sets.split(",")
        enrichr_library = get_library_name()
        gss = [ g for g in gss if g in enrichr_library]
        self._logger.info("Libraries are used: %s"%("',".join(gss)))
        if len(gss) < 1:
            sys.stderr.write("Not validated Enrichr library name provided\n")
            sys.stdout.write("Hint: use get_library_name() to view full list of supported names")
            sys.exit(1)
        self.results = pd.DataFrame()
        for g in gss:
            self._gs = str(g)
            self._logger.debug("Start Enrichr using library: %s" % (self._gs))
            self._logger.info('Analysis name: %s, Enrichr Library: %s' % (self.descriptions, self._gs))

            shortID, res = self.get_results(genes_list)
            # Remember gene set library used
            res["dataset"] = self._gs
            # Append to master dataframe
            self.results = self.results.append(res, ignore_index=True)
            self.res2d = res
            sleep(2)
            if self._outdir is None: continue

            self._logger.info('Save file of enrichment results: Job Id:' + str(shortID))
            outfile = "%s/%s.%s.%s.reports.txt" % (self.outdir, self._gs, self.descriptions, self.module)
            self.res2d.to_csv(outfile, index=False, encoding='utf-8')
            # plotting
            if not self.__no_plot:
                fig = barplot(df=res, cutoff=self.cutoff,
                              figsize=self.figsize,
                              top_term=self.__top_term,
                              color='salmon',
                              title='')
                if fig is None:
                    self._logger.warning("Warning: No enrich terms using library %s when cutoff = %s"%(self._gs, self.cutoff))
                else:
                    fig.savefig(outfile.replace("txt", self.format),
                                bbox_inches='tight', dpi=300)
            self._logger.info('Done.\n')
        # clean up tmpdir
        if self._outdir is None: self._tmpdir.cleanup()

        return

    def run_single(self):
        """run enrichr for one sample"""

        # read input file
        genes_str=self.parse_genelists()

        self._logger.info("Connecting to Enrichr Server to get latest library names")
        if self._gs in DEFAULT_LIBRARY:
            enrichr_library = DEFAULT_LIBRARY
        else:
            enrichr_library = get_library_name()
            if self._gs not in enrichr_library:
                sys.stderr.write("%s is not a Enrichr library name\n"%self._gs)
                sys.stdout.write("Hint: use get_library_name() to view full list of supported names")
                sys.exit(1)

        self._logger.info('Analysis name: %s, Enrichr Library: %s'%(self.descriptions, self._gs))

        # enrichr url
        ENRICHR_URL = 'http://amp.pharm.mssm.edu/Enrichr/addList'
        # Send gene list
        job_id = self.send_genes(genes_str, ENRICHR_URL)
        self._logger.debug('Job ID:'+ str(job_id))

        # check overlap genes
        ENRICHR_URL_A = 'http://amp.pharm.mssm.edu/Enrichr/view?userListId=%s'
        user_list_id = job_id['userListId']
        response_gene_list = requests.get(ENRICHR_URL_A % str(user_list_id), timeout=None)
        # wait for 1s
        sleep(1)
        if not response_gene_list.ok:
            raise Exception('Error getting gene list')

        self._logger.info('Submitted gene list:' + str(job_id))
        # Get enrichment results
        ENRICHR_URL = 'http://amp.pharm.mssm.edu/Enrichr/enrich'
        query_string = '?userListId=%s&backgroundType=%s'
        # get id data
        user_list_id = job_id['userListId']
        response = requests.get(ENRICHR_URL + query_string % (str(user_list_id), self._gs))
        if not response.ok:
            raise Exception('Error fetching enrichment results')

        self._logger.debug('Get enrichment results: Job Id:'+ str(job_id))
        # Download file of enrichment results
        ENRICHR_URL = 'http://amp.pharm.mssm.edu/Enrichr/export'
        query_string = '?userListId=%s&filename=%s&backgroundType=%s'
        user_list_id = str(job_id['userListId'])
        filename = "%s.%s.%s.reports"%(self._gs, self.descriptions, self.module)
        url = ENRICHR_URL + query_string % (user_list_id, filename, self._gs)

        # set max retries num =5
        s = retry(num=5)
        response = s.get(url, stream=True, timeout=None)

        self._logger.info('Downloading file of enrichment results: Job Id:'+ str(job_id))
        # with open(outfile, 'wb') as f:
        #     for chunk in response.iter_content(chunk_size=1024):
        #         if chunk:
        #             f.write(chunk)

        self._logger.debug('Results written to: ' + outfile)

        # save results
        df =  pd.read_table(StringIO(response.content.decode('utf-8')))
        self.res2d = df

        if self._outdir is None: return
        outfile="%s/%s.%s.%s.reports.txt"%(self.outdir, self._gs, self.descriptions, self.module)
        df.to_csv(outfile, index=False, sep='\t')
        # plotting
        if not self.__no_plot:
            fig = barplot(df=df, cutoff=self.cutoff,
                          figsize=self.figsize, 
                          top_term=self.__top_term,
                          color='salmon',
                          title='')
            if fig is None:
                self._logger.warning("Warning: No enrich terms using library %s when cutoff = %s"%(self._gs, self.cutoff))
            else:
                fig.savefig(outfile.replace("txt", self.format),
                            bbox_inches='tight', dpi=300)
        self._logger.info('Done.\n')
        return


def enrichr(gene_list, gene_sets, description='foo', outdir='Enrichr',
            cutoff=0.05, format='pdf', figsize=(8,6), top_term=10, no_plot=False, verbose=False):
    """Enrichr API.

    :param gene_list: Flat file with list of genes, one gene id per row, or a python list object
    :param gene_sets: Enrichr Library to query. Required enrichr library name(s). Separate each name by comma.
    :param description: name of analysis. optional.
    :param outdir: Output file directory
    :param float cutoff: Adjust P-value cutoff, for plotting. Default: 0.05
    :param str format: Output figure format supported by matplotlib,('pdf','png','eps'...). Default: 'pdf'.
    :param list figsize: Matplotlib figsize, accept a tuple or list, e.g. (width,height). Default: (6.5,6).
    :param bool no_plot: if equal to True, no figure will be draw. Default: False.
    :param bool verbose: Increase output verbosity, print out progress of your job, Default: False.

    :return: An Enrichr object, which obj.res2d contains your enrichr query.
    """
    enr = Enrichr(gene_list, gene_sets, description, outdir,
                  cutoff, format, figsize, top_term, no_plot, verbose)
    enr.run()

    return enr
