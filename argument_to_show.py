
import re
import logging

# from fuzzywuzzy import fuzz
from thetvdb_api import TheTVDBAPI


class Argument2Show:

    def __init__(self):
        self.tvdb_api = TheTVDBAPI()
        self.re_year = re.compile(r'({})'.format('|'.join(map(str, range(1900, 2040)))))

    def argument2show(self, argument_show):
        if argument_show.startswith('tt'):
            return self.tvdb_api.get_show_by_imdb_id(argument_show)

        year = self.re_year.search(argument_show)
        if year:
            argument_show = (argument_show[:year.start()] + argument_show[year.end():]).strip().replace('  ', ' ')
            year = year.group(1)

        return self._search_for_title(argument_show, year=year)

    def _search_for_title(self, title, year=None):
        shows = self.tvdb_api.get_shows_by_search(title, year=year)
        if shows:
            logging.debug('Found matches "{}" for argument "{}"'.format(','.join(map(str, shows)), title))

            # best_result, result_ratio = max(map(lambda r: (fuzz.token_set_ratio(r, title), r), results))
            best_result = max([s for s in shows if s.raw.get('status','') == 'Continuing'],
                              key=lambda s: len(s.raw.get('overview', '')))
            logging.debug('Best result for Argument "{}" is Show "{}"'.format(title, best_result))

            return best_result