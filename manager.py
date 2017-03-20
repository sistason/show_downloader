#!/usr/bin/env python3
import os
import asyncio
import logging

from a_argument_to_show.argument_to_show import Argument2Show
from b_show_to_status.show2status import Show2Status
from c_status_to_torrent.status2torrent import QUALITY_REGEX, Status2Torrent, GRABBER
from d_torrent_to_download.torrent2download import Torrent2Download, DOWNLOADERS


class Information:
    def __init__(self, download_directory):
        self.download_directory = download_directory
        self.show = None
        self.status = None
        self.torrents = None


class ShowManager:
    def __init__(self, download_directory, auth, update_missing=False,
                 quality=None, torrenter=None, downloader=None):
        self.download_directory = download_directory
        self.event_loop = asyncio.get_event_loop()

        self.arg2show = Argument2Show()
        self.show2status = Show2Status(update_missing)
        self.status2torrent = Status2Torrent(torrenter, quality, update_missing=update_missing)
        self.torrent2download = Torrent2Download(downloader, auth, self.event_loop)

    def _check_init(self):
        return bool(self.arg2show and self.show2status and self.status2torrent and self.torrent2download)

    def manage(self, show_arguments):
        if not self._check_init():
            logging.error('Initial setup of all components not possible, aborting...')
            return

        if not show_arguments:
            show_arguments = self.get_shows_from_directory()

        tasks = asyncio.gather(*[self._workflow(arg) for arg in show_arguments])
        self.event_loop.run_until_complete(tasks)
        self.close()

    async def _workflow(self, show_argument):
        show_infos = Information(self.download_directory)

        show_infos.show = self.arg2show.argument2show(show_argument)
        show_infos.status = self.show2status.analyse(show_infos)
        show_infos.torrents = await self.status2torrent.get_torrents(show_infos)
        await self.torrent2download.download(show_infos)

    def close(self):
        self.status2torrent.torrent_grabber.close()
        self.torrent2download.close()
        self.event_loop.close()

    def get_shows_from_directory(self):
        return [listing for listing in os.listdir(self.download_directory) if path.isdir(listing)]


if __name__ == '__main__':
    import argparse
    from os import path, access, W_OK, R_OK

    def argcheck_dir(string):
        if path.isdir(string) and access(string, W_OK) and access(string, R_OK):
            return path.abspath(string)
        raise argparse.ArgumentTypeError('{} is no directory or isn\'t writeable'.format(string))

    argparser = argparse.ArgumentParser(description="Manage your tv-show directories")
    argparser.add_argument('shows', nargs='*', type=str,
                           help='Manage these shows or let free to get the shows automatically from download_directory')
    argparser.add_argument('download_directory', type=argcheck_dir, default='.',
                           help='Set the directory to sort the file(s) into.')
    argparser.add_argument('-a', '--auth', type=str, required=True,
                           help="Either 'user:password' or a path to a pw-file with that format (for premiumize.me)")
    argparser.add_argument('-u', '--update_missing', action="store_true",
                           help="update shows, check if there are missing episode and download them")
    argparser.add_argument('-q', '--quality', type=str, choices=QUALITY_REGEX.get('quality').keys(),
                           help="Choose the quality of the episodes to download")
    argparser.add_argument('-e', '--encoder', type=str, choices=QUALITY_REGEX.get('encoder').keys(),
                           help="Choose the encoder of the episodes to download")
    argparser.add_argument('-t', '--torrenter', type=str, default='piratebay', choices=GRABBER.keys(),
                           help="Choose the encoder of the episodes to download")
    argparser.add_argument('-d', '--downloader', type=str, default='premiumize.me', choices=DOWNLOADERS.keys(),
                           help="Choose the encoder of the episodes to download")

    args = argparser.parse_args()
    quality_dict = {'quality': args.quality, 'encoder': args.encoder}
    torrenter_ = GRABBER.get(args.torrenter)
    downloader_ = DOWNLOADERS.get(args.downloader)

    logging.basicConfig(format='%(message)s',
                        level=logging.DEBUG)

    sm = ShowManager(args.download_directory, args.auth, update_missing=args.update_missing,
                     quality=quality_dict, torrenter=torrenter_, downloader=downloader_)
    sm.manage(args.shows)
