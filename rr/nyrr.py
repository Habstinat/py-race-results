import datetime
import http.cookiejar
import logging
import os
import re
import urllib.request
import warnings
import xml.etree.cElementTree as ET

from bs4 import BeautifulSoup

from .common import RaceResults, remove_namespace


class NewYorkRR(RaceResults):
    """
    Handles race results from New York Road Runners website.
    """
    def __init__(self, **kwargs):
        """
        """
        RaceResults.__init__(self)
        self.__dict__.update(**kwargs)

        # Need to remember the current URL.
        self.downloaded_url = None

        # Set the appropriate logging level.
        self.logger.setLevel(getattr(logging, self.verbose.upper()))

        self.cookie_jar = None

        # This URL is used in a regular expression that teases out the URLs
        # for all of the results.
        self.result_url_base = "http://web2.nyrrc.org/cgi-bin/start.cgi/"
        self.result_url_base += "aes-programs/results/startup.html"

    def run(self):
        """
        This page has the URLs for the recent results.
        """
        self.initialize_output_file()

        url = 'http://web2.nyrrc.org'
        url += '/cgi-bin/start.cgi/aes-programs/results/resultsarchive.htm'

        local_file = 'resultsarchive.html'
        self.download_file(url, local_file)

        # There are two forms used for searches.  The one that we want (list
        # all the results for an entire year) is the 2nd on that this regex
        # retrieves.
        with open(local_file) as fp:
            html = fp.read()
        regex = re.compile(r"""<form
                               \s+name="(?P<name>\w+)"
                               \s+method=post
                               \s+action=(?P<action>\S+)
                               .*\s""", re.VERBOSE)
        m = regex.findall(html)
        if len(m) != 2:
            msg = "resultsarchive did not yield right number of results."
            raise RuntimeError(msg)
        url = m[0][1]

        # The page for POSTing the search needs POST params.
        post_params = {}
        post_params['NYRRYEAR'] = str(self.start_date.year)
        post_params['AESTIVACVNLIST'] = 'NYRRYEAR'
        data = urllib.parse.urlencode(post_params)
        data = data.encode()

        # Download the race list page for the specified year
        local_file = 'nyrrraces.html'
        self.download_file(url, local_file, data)

        # This is not valid HTML.  Need to get rid of some bad FORMs,
        # none of which are needed.
        with open(local_file, 'r', encoding='utf-8') as fp:
            html = fp.read()
        html = html.replace('form', 'div')
        with open(local_file, 'w') as f:
            f.write(html)

        self.local_tidy(local_file)

        # Parse out the list of races.  They are all in a
        # particular table.
        with open(local_file, 'r') as f:
            markup = f.read()

        pattern = r"""<a\shref="(?P<url>{0}         # This part too long
                      \?result.id=
                      (?P<result_id>[0-9a-z]*)&amp; # Unique for each result.
                      result.year=\d\d\d\d)">       # End of URL
                      (?P<race_name>.*?)            # Name of the race.
                      </a>\s*                       # End of anchor.
                      (?P<month>\d\d)/
                      (?P<day>\d\d)/
                      (?P<year>\d\d)"""             # Race date.
        pattern = pattern.format(self.result_url_base)
        regex = re.compile(pattern, re.VERBOSE | re.DOTALL)
        for matchobj in regex.finditer(markup):

            url = matchobj.group('url')
            url = re.sub('&amp;', '&', url)

            # Get rid of leading and trailing white space in the race name.
            race_name = matchobj.group('race_name')
            race_name = re.sub('^\s*', '', race_name)
            race_name = re.sub('\s*$', '', race_name)

            race_date = datetime.date(int(matchobj.group('year')) + 2000,
                                      int(matchobj.group('month')),
                                      int(matchobj.group('day')))

            if self.start_date <= race_date and race_date <= self.stop_date:
                self.logger.info("Keeping {0}".format(race_name))
                self.process_event(url)
            else:
                self.logger.info("Skipping %s" % race_name)

    def process_event(self, url):
        """We have the URL of a single event.  The URL does not lead to the
        results, however, it leads to a search page.
        """
        local_file = 'event_search.html'
        self.download_file(url, local_file)

        try:
            with open(local_file, 'r', encoding='utf-8') as fp:
                markup = fp.read()
        except UnicodeDecodeError:
            with open(local_file, 'r', encoding='latin1') as fp:
                markup = fp.read()

        # There should be a single form.
        regex = re.compile(r"""<form\s*
                               method=post\s*
                               action=(?P<action>.*?)\s*
                               >""", re.VERBOSE | re.DOTALL)
        matchobj = regex.search(markup)
        if matchobj is None:
            warnings.warn("Unable to match the expected form.")
        url = matchobj.group('action')

        # The page for POSTing the search needs POST params.
        # Provide all the search parameters for this race.  This includes, most
        # importantly, the team code, i.e. RARI for Raritan Valley Road
        # Runners.
        post_params = {}
        post_params['search.method'] = 'search.team'
        post_params['input.lname'] = ''
        post_params['input.fname'] = ''
        post_params['input.bib'] = ''
        post_params['overalltype'] = 'All'
        post_params['input.agegroup.m'] = '12 to 19'
        post_params['input.agegroup.f'] = '12 to 19'
        post_params['teamgender'] = ''
        post_params['team_code'] = self.team
        post_params['items.display'] = '500'
        post_params['AESTIVACVNLIST'] = 'overalltype,input.agegroup.m,'
        post_params['AESTIVACVNLIST'] += 'input.agegroup.f,teamgender'
        post_params['AESTIVACVNLIST'] += 'team_code'
        data = urllib.parse.urlencode(post_params)
        data = data.encode()

        local_file = 'nyrrresult.html'
        self.download_file(url, local_file, data)
        self.local_tidy(local_file)

        # Use Beautifulsoup/lxml to make it compliant.
        with open(local_file, 'r', encoding='utf-8') as fp:
            markup = fp.read()
            soup = BeautifulSoup(markup, 'lxml')
        with open(local_file, 'w') as fp:
            fp.write(soup.prettify())

        # If there were no results for the specified team, then the html will
        # contain some red text to the effect of "Your search returns no
        # match."
        with open(local_file, 'r', encoding='utf-8') as fp:
            html = fp.read()
        if re.search("Your search returns no match.", html) is not None:
            return

        # So now we have a result.  Parse it for the result table.
        root = ET.parse(local_file).getroot()
        root = remove_namespace(root)

        # 3rd table is the one we want.
        pattern = './/table'
        tables = root.findall(pattern)

        if len(tables) < 3:
            return

        div = self.webify_results(tables)
        self.insert_race_results(div)

    def webify_results(self, tables):
        """Turn the results into the output form that we want.
        """

        # maybe abstract this into a webify function.
        div = ET.Element('div')
        div.set('class', 'race')
        hr = ET.Element('hr')
        hr.set('class', 'race_header')
        div.append(hr)

        # Append the race metadata.
        tds = tables[1].findall('.//td')
        td = tds[2]
        race_meta = ET.Element('div')
        ch = td.getchildren()
        race_meta.append(ch[0])
        race_meta.append(ch[1])
        race_meta.append(ch[2])
        race_meta.append(ch[3])
        div.append(race_meta)

        # Append the URL from whence we came..
        pdiv = ET.Element('div')
        pdiv.set('class', 'provenance')
        span = ET.Element('span')
        span.text = 'Results courtesy of '
        pdiv.append(span)
        anchor = ET.Element('a')
        anchor.set('href', 'http://www.nyrr.org')
        anchor.text = 'New York Road Runners'
        pdiv.append(anchor)
        span = ET.Element('span')
        span.text = '.'
        pdiv.append(span)
        div.append(pdiv)

        # The table we want is the 3rd one.  We need
        # to sanitize it, though.
        table = self.sanitize_table(tables[3])
        div.append(table)
        return div

    def sanitize_table(self, old_table):
        """The table as-is has a few links that we need to remove.
        """
        new_table = ET.Element('table')
        new_table.set('cellpadding', '3')
        new_table.set('cellspacing', '0')
        new_table.set('border', '1')

        new_tr = ET.Element('tr')
        new_tr.set('bgcolor', '#EEEEEE')

        trs = old_table.getchildren()
        tr = trs[0]
        old_tds = tr.getchildren()

        # 1st two TD elements need to be replaced.
        td = ET.Element('td')
        td.text = old_tds[0].getchildren()[0].text
        new_tr.append(td)

        # 1st two TD elements need to be replaced.
        td = ET.Element('td')
        td.text = old_tds[1].getchildren()[0].text
        new_tr.append(td)

        # Append the rest of the TD elements in the first row.
        for td in old_tds[2:]:
            new_tr.append(td)
        new_table.append(new_tr)

        # And append the race results as-is.
        for tr in trs[1:]:
            new_table.append(tr)

        return(new_table)

    def local_tidy(self, local_file=None):
        """
        Tidy up the HTML.
        """
        try:
            with open(local_file, encoding='utf-8') as fp:
                markup = fp.read()
        except UnicodeDecodeError:
            with open(local_file, encoding='iso-8859-1') as fp:
                markup = fp.read()
        #soup = BeautifulSoup(markup, "lxml")
        soup = BeautifulSoup(markup, "html.parser")

        import codecs
        fp = codecs.open(local_file, encoding='utf-8', mode='w')
        fp.write(soup.prettify())
        fp.close()

    def download_file(self, url, local_file, params=None):
        """
        Download a URL to a local file.

        Args
        ----
            url:  The URL to retrieve
            local_file:  Name of the file where we will store the web page.
            params:  POST parameters to supply
        """
        # cookie support needed for NYRR results.
        if self.cookie_jar is None:
            self.cookie_jar = http.cookiejar.LWPCookieJar()
        cookie_processor = urllib.request.HTTPCookieProcessor(self.cookie_jar)
        opener = urllib.request.build_opener(cookie_processor)
        urllib.request.install_opener(opener)

        headers = {'User-Agent': self.user_agent}
        req = urllib.request.Request(url, None, headers)
        response = urllib.request.urlopen(req, params)
        html = response.readall()

        with open(local_file, 'wb') as f:
            f.write(html)
