"""
Backend class for handling CoolRunning race results.
"""

import datetime
import logging
import os
import re
import sys
import warnings

from lxml import etree

from .common import RaceResults, remove_namespace


class CoolRunning(RaceResults):
    """
    Class for handling CoolRunning Race Results.
    """
    def __init__(self, states=['ma'], **kwargs):
        """
        base_url:
        memb_list:  membership list
        race_list:  file containing list of races
        output_file:  final race results file
        verbose:  how much output to produce
        """
        RaceResults.__init__(self)
        self.__dict__.update(**kwargs)
        self.states = states

        self.base_url = 'http://www.coolrunning.com'

        self.load_membership_list()

        # Set the appropriate logging level.
        self.logger.setLevel(getattr(logging, self.verbose.upper()))

    def run(self):
        self.compile_results()

    def compile_results(self):
        """
        Either download the requested results or go through the
        provided list.
        """
        self.initialize_output_file()
        if self.race_list is None:
            self.compile_web_results()
        else:
            self.compile_local_results()

    def compile_web_results(self):
        """
        Download the requested results and compile them.
        """
        for state in self.states:
            self.download_state_master_file(state)
            self.process_state_master_file(state)

    def construct_state_match_pattern(self, state):
        """
        Want to match strings like

        http://www.coolrunning.com/results/07/ma/Jan16_Coloni_set1.shtml

        So we construct a regular expression to match against
        all the dates in the specified range.
        """

        #pattern = 'http://ww.coolrunning.com/results/'
        pattern = '/results/'
        pattern += self.start_date.strftime('%y')
        pattern += '/'
        pattern += state
        pattern += '/'
        pattern += self.start_date.strftime('%b')

        # continue with a regexp to match any of the days in the range.
        # It's a non-capturing group.
        day_range = '(?:'
        for day in range(self.start_date.day, self.stop_date.day):
            day_range += "%d_|" % day
        day_range += '%d_)' % self.stop_date.day

        pattern += day_range

        pattern += '.*shtml'
        self.logger.debug('Match pattern is %s' % pattern)
        r = re.compile(pattern)
        return(r)

    def process_state_master_file(self, state):
        """
        Compile results for the specified state.
        We assume that we have the state file stored locally.
        """
        local_state_file = state + '.shtml'
        regex = self.construct_state_match_pattern(state)

        with open(local_state_file, 'r') as f:
            markup = f.read()

        relative_urls = regex.findall(markup)

        for relative_url in relative_urls:

            top_level_url = 'http://www.coolrunning.com' + relative_url
            race_file = top_level_url.split('/')[-1]
            self.logger.info(top_level_url)
            self.download_file(top_level_url, local_file=race_file)
            self.compile_race_results(race_file)

            # Now collect any secondary result files.
            with open(race_file) as f:
                markup = f.read()

            # construct the secondary pattern.  If the race name is something
            # like "TheRaceSet1.shtml", then the secondary races will be
            # "TheRaceSet[2345].shmtl" etc.
            parts = race_file.split('.')
            base = parts[-2][0:-1]
            pat = '<a href="(?P<inner_url>\.\/' + base + '\d+\.shtml)">'
            inner_regex = re.compile(pat)
            for matchobj in inner_regex.finditer(markup):

                relative_inner_url = matchobj.group('inner_url')
                if relative_inner_url in top_level_url:
                    # Already seen this one.
                    continue

                # Strip off the leading "./" to get the name we use for the
                # local file.
                race_file = relative_inner_url[2:]

                # Form the full inner url by swapping out the top level
                # url
                lst = top_level_url.split('/')
                lst[-1] = race_file
                inner_url = '/'.join(lst)
                self.logger.info(inner_url)
                self.download_file(inner_url, local_file=race_file)
                self.compile_race_results(race_file)

    def compile_vanilla_results(self, race_file):
        """
        Compile race results for vanilla CoolRunning races.
        """
        with open(race_file, 'r') as f:
            markup = f.read()

        regex = re.compile(r"""<pre>              # banner follows the <pre>
                               (?P<race_text>.*?) # regex should NOT be greedy!
                               </pre>""",
                           re.VERBOSE | re.IGNORECASE | re.DOTALL)
        matchobj = regex.search(markup)
        if matchobj is None:
            warnings.warn('Vanilla CRRR regex did not match.')
            return []

        text = matchobj.group('race_text')
        results = []
        for line in text.split('\n'):
            if self.match_against_membership(line):
                results.append(line)

        return results

    def compile_ccrr_race_results(self, race_file):
        """
        This is the format generally used by Cape Cod
        Road Runners.

        Return value:
            List of <TR> elements, each row containing an individual result.
        """
        pattern = './/body/table/tr/td/table/tr/td/table/tr/td/div/table/tr'
        parser = etree.HTMLParser()
        tree = etree.parse(race_file, parser)
        root = tree.getroot()
        root = remove_namespace(root)

        trs = root.findall(pattern)

        results = []
        for tr in trs:
            tds = tr.getchildren()

            if len(tds) < 3:
                continue

            runner_name = tds[1].text
            if runner_name is None:
                continue
            for idx in range(0, len(self.first_name_regex)):
                fregex = self.first_name_regex[idx]
                lregex = self.last_name_regex[idx]
                if fregex.search(runner_name) and lregex.search(runner_name):
                    results.append(tr)

        if len(results) > 0:
            # Prepend the header.
            results.insert(0, trs[0])

        return results

    def get_author(self, race_file):
        """
        Get the race company identifier.

        Example
        -------
            <meta name="Author" content="colonial" />
        """
        regex1 = re.compile(r"""<meta\s
                                name=\"Author\"\s
                                content=\"(?P<content>.*)\"\s*
                                \/?>""",  # Sometimes there's no /
                            re.VERBOSE | re.IGNORECASE)
        regex2 = re.compile(r"""<meta\s
                                content=\"(?P<content>.*)\"\s*
                                name=\"Author\"\s*
                                \/?>""",  # Sometimes there's no /
                            re.VERBOSE | re.IGNORECASE)
        with open(race_file, 'rt') as fptr:
            html = fptr.read()
            matchobj = regex1.search(html)
            if matchobj is not None:
                return matchobj.group('content')

            matchobj = regex2.search(html)
            if matchobj is not None:
                return matchobj.group('content')
            else:
                msg = "Could not parse the race company identifier"
                raise RuntimeError(msg)

    def compile_race_results(self, race_file):
        """
        Go through a race file and collect results.
        """
        html = None
        variant = self.get_author(race_file)
        self.logger.info('Variant is {0}'.format(variant))
        if variant in ['CapeCodRoadRunners']:
            self.logger.debug('Cape Cod Road Runners pattern')
            results = self.compile_ccrr_race_results(race_file)
            if len(results) > 0:
                html = self.webify_ccrr_results(results, race_file)
                self.insert_race_results(html)
        elif variant in ['kick610', 'JB Race', 'gstate', 'ab-mac', 'FTO',
                         'NSTC', 'ndatrackxc', 'wcrc',
                         'Spitler']:
            # Assume the usual coolrunning pattern.
            self.logger.debug('Vanilla Coolrunning pattern')
            results = self.compile_vanilla_results(race_file)
            if len(results) > 0:
                html = self.webify_vanilla_results(results, race_file)
                self.insert_race_results(html)
        elif variant in ['colonial', 'opportunity']:
            # 'colonial' is a local race series.  Gawd-awful
            # excel-to-bastardized-html.  The hell with it.
            #
            # 'opportunity' seems to be CMS 52 Week Series
            self.logger.info('Skipping {0} race series.'.format(variant))
        elif variant in ['Harriers']:
            self.logger.info('Skipping harriers (snowstorm classic?) series.')
        elif variant in ['FFAST', 'lungne', 'northeastracers', 'sri']:
            msg = 'Skipping {0} pattern (unhandled XML pattern).'
            self.logger.info(msg.format(variant))
        elif variant in ['WCRCSCOTT']:
            msg = 'Skipping {0} XML pattern (looks like a race series).'
            self.logger.info(msg.format(variant))
        else:
            msg = 'Unknown pattern (\"{0}\"), going to try vanilla CR parsing.'
            self.logger.warning(msg.format(variant))
            results = self.compile_vanilla_results(race_file)
            if len(results) > 0:
                html = self.webify_vanilla_results(results, race_file)
                self.insert_race_results(html)

    def construct_common_div(self, race_file):
        """
        Construct an XHTML element to contain race results.
        """
        div = etree.Element('div')
        div.set('class', 'race')
        hr = etree.Element('hr')
        hr.set('class', 'race_header')
        div.append(hr)

        with open(race_file, 'r') as f:
            markup = f.read()

        # The H1 tag has the race name.  The H2 tag has the location and date.
        # Both are the only such tabs in the file.
        #
        # Use re.DOTALL since . must match across lines.
        regex = re.compile('<h1>(?P<h1>.*)</h1>.*<h2>(?P<h2>.*)</h2>',
                           re.DOTALL)
        matchobj = regex.search(markup)
        if matchobj is None:
            msg = "Could not find H1/H2 tags in {0}".format(race_file)
            raise RuntimeError(msg)

        h1 = etree.Element('h1')
        h1.text = matchobj.group('h1')
        div.append(h1)

        h2 = etree.Element('h2')
        h2.text = matchobj.group('h2')
        div.append(h2)

        # Append the URL if possible.
        if self.downloaded_url is not None:
            p = etree.Element('p')
            span = etree.Element('span')
            span.text = 'Complete results '
            p.append(span)
            a = etree.Element('a')
            a.set('href', self.downloaded_url)
            a.text = 'here'
            p.append(a)
            span = etree.Element('span')
            span.text = ' on Coolrunning.'
            p.append(span)
            div.append(p)

        return(div)

    def webify_ccrr_results(self, results, race_file):
        """
        Turn the list of results into full HTML.
        This works for Cape Cod Road Runners formatted results.

        Return value:
            "finished" HTML for the race.
        """
        div = self.construct_common_div(race_file)

        table = etree.Element('table')
        for tr in results:
            if tr is not None:
                table.append(tr)

        div.append(table)
        return div

    def webify_vanilla_results(self, result_lst, race_file):
        """
        Insert CoolRunning results into the output file.
        """
        div = self.construct_common_div(race_file)

        pre = etree.Element('pre')
        pre.set('class', 'actual_results')

        banner_text = self.parse_banner(race_file)

        text = '<pre class="actual_results">\n'
        text += banner_text + '\n'.join(result_lst) + '\n'
        text += '</pre>'
        pre = etree.XML(text)
        div.append(pre)

        return div

    def parse_banner(self, race_file):
        """
        Tease out the "banner" from the race file.

        This will usually be found following the <pre> tag that contains the
        results.
        """
        with open(race_file, 'r') as f:
            markup = f.read()
        regex = re.compile(r"""<pre>             # banner text follows
                               (?P<banner>.*?\n) # regex should NOT be greedy!
                               \s*1\b            # stop matching upon 1st place
                               .*                # the results are here
                               </pre>""",        # stop here
                           re.VERBOSE | re.IGNORECASE | re.DOTALL)
        matchobj = regex.search(markup)
        banner_text = matchobj.group('banner')

        # Clean it up if necessary.  Some of the clumsier race directors will
        # put ampersands into the text without making them XML entities.
        #regex = re.compile(r"""&                    # match the ampersand,
        #                       (?!                  # negative lookahead
        #                         [A-Za-z]+[0-9]*;   # named entity
        #                        | 
        #                         #[0-9]+;             # decimal entity
        #                        |
        #                         #x[0-9a-fA-F]+;)""", # hexidecimal entity
        #                   re.VERBOSE)
        banner_text = re.sub(r'&(?![A-Za-z]+[0-9]*;|#[0-9]+;|#x[0-9a-fA-F]+;)',
                             r'&amp;', banner_text)
        return banner_text

    def match_against_membership(self, line):
        """
        We have a line of text from the URL.  Match it against the
        membership list.
        """
        for idx in range(0, len(self.first_name_regex)):
            fregex = self.first_name_regex[idx]
            lregex = self.last_name_regex[idx]
            if fregex.search(line) and lregex.search(line):
                return(True)
        return(False)

    def download_state_master_file(self, state):
        """
        Download results for the specified state.

        The URL will have the pattern

        http://www.coolrunning.com/results/[YY]/[STATE].shtml

        """
        self.logger.info('Processing %s...' % state)
        state_file = '{0}.shtml'.format(state)
        url = 'http://www.coolrunning.com/results/{0}/{1}'
        url = url.format(self.start_date.strftime('%y'), state_file)
        self.logger.info('Downloading {0}.'.format(url))
        self.download_file(url, local_file=state_file)

    def download_race(self, anchor, inner_url=False, state=''):
        """
        """
        href = anchor.get('href')

        if inner_url:
            pattern = '/results/'
            pattern += self.start_date.strftime('%y')
            pattern += '/'
            pattern += state
            href = pattern + '/' + href

        url = 'http://www.coolrunning.com/%s' % href
        local_file = href.split('/')[-1]
        self.logger.info('Downloading %s...' % url)
        self.download_file(url, local_file=local_file)
        self.downloaded_url = url

        return(local_file)

    def compile_local_results(self):
        """
        Compile results from list of local files.
        """
        with open(self.race_list) as fp:
            for racefile in fp.readlines():
                racefile = racefile.rstrip()
                try:
                    self.logger.info('Processing file %s' % racefile)
                    self.compile_race_results(racefile)
                except IOError:
                    fmt = 'Encountered an error processing %s, skipping it.'
                    self.logger.debug(fmt % racefile)
