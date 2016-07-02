__all__ = ['AffliationChecker', 'Members', 'Articles', 'load_members_from_google_sheets', 'uft8_char_to_tex',\
        'name_formatter_html', 'name_formatter_tex', 'AuthorsFormatter', 'entry_formatter_tex', 'entry_formatter_html']

import cPickle as pickle
from operator import itemgetter
from itertools import imap, izip
from collections import defaultdict
from urllib import urlopen

import ads
import bibtexparser


def _isstring(s):
    try:
        s + ''
    except (ValueError, TypeError):
        return False
    return True


class AffliationChecker():
    def __init__(self, affliations={'stanford', 'slac', 'kipac', 'kavli institute for particle astrophysics and cosmology'},\
                 collaborations={'planck', 'fermi', 'bicep2', 'des'},
                 journals_no_affli={'arXiv', 'Sci', 'ARNPS', 'PNAS', 'PhPro', 'SPIE'}):
        self._affli = set(affliations)
        self._collab = set(collaborations)
        self._journals = set(journals_no_affli)

    def __call__(self, ads_article, author_index=0):
        try:
            aff = ads_article.aff[author_index].lower()
        except IndexError:
            aff = u'-'
        if aff == u'-':
            fa = ads_article.author[0].lower()
            journal = ads_article.bibcode[4:9].strip(u'.')
            if 'collaboration' in fa and any(_ in fa for _ in self._collab):
                return True
            elif journal in self._journals:
                return 'VERIFY'
        elif any(_ in aff for _ in self._affli):
            return True
        return False


class Members():
    def __init__(self):
        self._d = {}
        self.__getitem__ = self._d.__getitem__
        self.__iter__ = self._d.itervalues
        self.__len__ = len(self._d)

    def update(self, key, display_name, last_name, first_initial, ads_queries):
        short_name = last_name + ', ' + first_initial[:1]
        self._d[key] = dict(key=key, sn=short_name, q=ads_queries, dn=display_name)

    def add(self, key, display_name, last_name, first_initial, ads_queries):
        if key in self._d:
            raise ValueError
        self.update(key, display_name, last_name, first_initial, ads_queries)


uft8_char_to_tex = {u'\xaf':'-', u'\xb1':r'\pm',
                    u'\u0107':r"\'{c}", u'\xe9': r"\'{e}", u'\xe1': r"\'{a}", u'\xfc': r'\"{u}',
                    u'\xed': u"\\'{\\i}", u'\xf8': u'\\o',}


class Articles():
    def __init__(self, exclude_proceedings={'AAS', 'APS', 'IAUS', 'IAUGA', 'TESS', 'AGUFM', 'DPS', 'atnf', 'hst', 'mgm', 'tybp'},\
                 exclude_titles={'Erratum', 'Corrigendum'}, affliation_shifts={u'2015ApJ...806..206A':1}, \
                 query_constraints={'pubdate':'["2014-09-00" TO "2015-08-99"]', 'database':'("astronomy" OR "physics")'}, \
                 ads_fields=['author', 'aff', 'bibcode', 'title', 'pubdate', 'first_author'], \
                 affliation_checker=AffliationChecker()):
        self._exclude_proceedings = set(exclude_proceedings)
        self._exclude_titles = set(exclude_titles)
        self._affliation_shifts = affliation_shifts
        self._query_constraints = query_constraints
        self._affliation_checker = affliation_checker
        self._ads_fields = list(ads_fields)
        self._d = {}
        self._bib = {}

    def add(self, member):
        count = 0
        for query in member['q']:
            search_query = ads.SearchQuery(q=query, fl=self._ads_fields, **self._query_constraints)
            search_query.execute()
            for entry in search_query:
                if any(imap(entry.title[0].startswith, self._exclude_titles)) or entry.bibcode[4:9].strip('.') in self._exclude_proceedings:
                    continue
                aff_shift_idx = self._affliation_shifts.get(entry.bibcode, 0)
                for i, a in enumerate(entry.author):
                    if a.replace('-', ' ').lower().startswith(member['sn'].lower()):
                        res = self._affliation_checker(entry, i+aff_shift_idx)
                        if res:
                            break # found member
                else:
                    continue #did not find member
                if entry.bibcode not in self._d:
                    self._d[entry.bibcode] = dict(key=entry.bibcode, fa=entry.first_author, na=len(entry.author), t=entry.title[0], d=entry.pubdate[:7], m=dict(), q=set())
                self._d[entry.bibcode]['m'][member['key']] = i
                if not isinstance(res, bool): #require verification
                    self._d[entry.bibcode]['q'].add(member['key'])
                count += 1
        return count

    def remove(self, bibcodes):
        if _isstring(bibcodes):
            bibcodes = [bibcodes]
        bibcodes = set(bibcodes)
        keys = self._d.keys()
        for k in keys:
            del self._d[k]

    def white_list(self, bibcodes):
        if _isstring(bibcodes):
            bibcodes = [bibcodes]
        for k in bibcodes:
            if k in self._d:
                self._d[k]['q'].clear()

    def whiten_member_collab(self, min_member_number=3):
        for d in self._d.itervalues():
            if len(d['q']) >= min_member_number:
                d['q'].clear()

    def get_count(self, arxiv_only=False):
        if arxiv_only:
            return sum(1 for k in self._d.iterkeys() if k[4:9] == 'arXiv')
        return len(self._d)

    def get_bibcodes(self):
        return self._d.keys()

    def save(self, filename):
        with open(filename, 'w') as f:
            pickle.dump({'d':self._d, 'bib':self._bib}, f, pickle.HIGHEST_PROTOCOL)

    def load(self, filename, update=True):
        with open(filename, 'r') as f:
            d = pickle.load(f)
        if update:
            self._d.update(d['d'])
            self._bib.update(d['bib'])
        else:
            self._d = d['d']
            self._bib = d['bib']

    def update_bib(self):
        bibcodes = list(set(self.get_bibcodes())-set(self._bib.keys()))
        if bibcodes:
            self._bib.update(bibtexparser.loads(ads.ExportQuery(bibcodes, 'bibtex').execute()).entries_dict)

    def get_require_verification(self):
        output = []
        for d in self._d.itervalues():
            if not d['q']:
                continue
            k = d['key']
            output.append(dict(to_verify=d['q'], title=d['t'], first_author=d['fa'], journal=k[4:9].strip('.'), bibcode=k))
        return output

    def generate_formatted_output(self, authors_formatter, entry_formatter, encode="utf-8", encode_error_handling_dict=uft8_char_to_tex):
        self.update_bib()
        output = []
        for entry in sorted(self._d.itervalues(), key=itemgetter('d')):
            l = entry_formatter(self._bib, entry, authors_formatter(entry))
            try:
                l = l.encode(encode)
            except UnicodeEncodeError:
                l = ''.join(encode_error_handling_dict.get(c, c) for c in l)
            output.append(l)
        return output



def load_members_from_google_sheets(url):
    url = 'https://docs.google.com/spreadsheets/d/1Ok5i25gibuLTHoRhGhmNMqTplMh--0-i4L_8cxzkUxI/export?format=csv&gid=0'
    lines = urlopen(url).readlines()
    header = lines.pop(0).strip().split(',')
    members = Members()

    for line in lines:
        row = dict(izip(header, line.strip().split(',')))
        dn = row['print'] or row['name']
        last = row['last'].replace('-', ' ')
        firsts = row['first'].split(';')
        assert all(first[0]==row['first'][0] for first in firsts)
        fi = row['first'][0]
        q = ['=author:"{}, {}"'.format(last, first.replace('.', '*')) for first in firsts]
        if row['manual_add']:
            q.extend('bibcode:{}'.format(_) for _ in row['manual_add'].split(';'))
        members.add(row['name'], dn, last, fi, q)

    return members


def name_formatter_html(n):
    return '<b>{}</b>'.format(n)


def name_formatter_tex(n):
    return '\\name{{{}}}'.format(n.replace(' ', '~'))


class AuthorsFormatter():
    def __init__(self, name_formatter, name_replacing_dict={}):
        self._name_formatter = name_formatter
        self._name_replacing_dict = name_replacing_dict

    def _format_name(self, name):
        return self._name_formatter(self._name_replacing_dict.get(name, name))

    def __call__(self, entry):
        d = entry
        ka = ', '.join(self._format_name(m[0]) for m in sorted(d['m'].iteritems(), key=itemgetter(1)))
        try:
            i = d['m'].values().index(0)
        except ValueError:
            fa = d['fa'].partition(',')[0]
            if d['na'] == len(d['m']) + 1:
                a = u'{}, {}'.format(fa, ka)
            elif u'collaboration' in fa.lower():
                a = u'{}, with {}'.format(fa, ka)
            else:
                a = u'{} et al., with {}'.format(fa, ka)
        else:
            if d['na'] == len(d['m']):
                a = ka
            elif len(d['m']) == 1:
                a = u'{} et al'.format(ka)
            else:
                a = u'{0[0]} et al., with {0[2]}'.format(ka.partition(', '))
        return a


def entry_formatter_tex(bib, entry, formatted_authors):
    d = entry
    k = entry['key']
    journal = k[4:9].strip('.').replace('&', '\\&')
    if 'doi' in bib[k]:
        url = 'http://dx.doi.org/{}'.format(bib[k]['doi'])
    elif 'eprint' in bib[k]:
        url = 'http://arxiv.org/abs/{}'.format(bib[k]['eprint'])
    else:
        url = 'https://ui.adsabs.harvard.edu/#abs/{}/abstract'.format(k)
    return '\entry{' + '}{'.join((bib[k]['title'], formatted_authors, journal, url)) + '}'


def entry_formatter_html(bib, entry, formatted_authors):
    d = entry
    k = entry['key']
    journal = k[4:9].strip('.').replace('&', '&amp;')
    link = ''
    if 'doi' in bib[k]:
        link += '[<a href="http://dx.doi.org/{}">{}</a>]'.format(bib[k]['doi'], journal)
    if 'eprint' in bib[k]:
        link += '[<a href="http://arxiv.org/abs/{}">arXiv</a>]'.format(bib[k]['eprint'])
    link += '[<a href="https://ui.adsabs.harvard.edu/#abs/{}/abstract">ADS</a>]'.format(k)
    return u'<li><i>"{},"</i> {}. {}</li>'.format(d['t'], formatted_authors, link)

