import datetime
import asyncio
import logging
import os

from a_argument_to_show.thetvdb_api import Season
from premiumize_me_dl.premiumize_me_api import PremiumizeMeAPI
DOWNLOADERS = {'premiumize.me': PremiumizeMeAPI, 'default': PremiumizeMeAPI}


class Download:
    def __init__(self, information, reference, transfer, downloader):
        self.information = information
        self.reference = reference
        self.downloader = downloader

        self.transfer = None
        self.start_time = None

        self.retries = 10


class Torrent2Download:
    CHECK_EVERY = 2
    WORKER_PER_SHOW = 5

    def __init__(self, login, event_loop):
        self.event_loop = event_loop
        downloader = DOWNLOADERS.get('default')
        self.torrent_downloader = downloader(login, self.event_loop)

        self.downloads_queue = asyncio.Queue()
        self.all_workers = []

        self.transfers = []
        self.shutdown = False
        self._transfers_updater = None

    async def close(self):
        self.shutdown = True
        [w.cancel() for w in self.all_workers]

        if self._transfers_updater is not None:
            for _ in range(self.CHECK_EVERY*2):
                if self._transfers_updater.done():
                    break
                await asyncio.sleep(1)

        asyncio.wait(self.all_workers, timeout=self.CHECK_EVERY)

        await self.torrent_downloader.close()

    async def download_from_cache(self, information):
        if self._transfers_updater is None:
            await self._wait_for_transfers()

        show_transfers = [transfer for transfer in self.transfers if information.show.name in transfer.name]
        for show_transfer in show_transfers:
            for episode in information.status.episodes_missing:
                if episode.get_regex().search(show_transfer.name):
                    print('Found ep {} in transfer {}'.format(episode.episode, show_transfer.name))
                    if await self.torrent_downloader.download_transfer(show_transfer, information.download_directory):
                        information.status.episodes_missing.remove(episode)
                        await self.torrent_downloader.delete(show_transfer)

            for season in information.status.seasons_missing:
                if season.get_regex().search(show_transfer.name):
                    print('Found se {} in transfer {}'.format(season.number, show_transfer.name))
                    if await self.torrent_downloader.download_transfer(show_transfer, information.download_directory):
                        information.status.seasons_missing.remove(season)
                        await self.torrent_downloader.delete(show_transfer)

        return information

    async def _update_transfers(self):
        self.transfers = await self.torrent_downloader.get_transfers()
        for _ in range(self.CHECK_EVERY):
            if self.shutdown:
                return
            await asyncio.sleep(1)
        asyncio.ensure_future(self._update_transfers())

    async def download(self, information):
        logging.info('Downloading {}...'.format(information.show.name))

        await self._start_torrenting(information)

        workers = [asyncio.ensure_future(self.worker()) for _ in range(self.WORKER_PER_SHOW)]
        self.all_workers.extend(workers)
        await asyncio.gather(*workers)

    async def _start_torrenting(self, information):
        logging.debug('Start torrenting {}...'.format(information.show.name))

        #FIXME: is the list the same reference for all tasks? call by value or reference?
        transfer_list_ = []
        tasks = [asyncio.ensure_future(self._upload_torrent(torrent, transfer_list_, information))
                        for torrent in information.torrents]
        await asyncio.gather(*tasks)

        if self._transfers_updater is None:
            await self._wait_for_transfers()

    async def _wait_for_transfers(self):
        self._transfers_updater = asyncio.ensure_future(self._update_transfers())
        for _ in range(10):
            if self.transfers:
                break
            await asyncio.sleep(1)
        else:
            logging.warning('Could not get torrent-transfers in time!')

    async def _upload_torrent(self, torrent, transfer_list_, information):
        if torrent is None:
            return
        for link in torrent.links:
            transfer = await self.torrent_downloader.upload(link)
            if transfer is not None:
                if transfer.id not in transfer_list_:
                    transfer_list_.append(transfer.id)
                    download = Download(information, torrent.reference, transfer, self.torrent_downloader)
                    await self.downloads_queue.put(download)
                    return
                logging.warning('Link "{}" for "{}" was a duplicate'.format(link[:50], torrent.reference))

    async def worker(self):
        try:
            while True:
                download = self.downloads_queue.get_nowait()
                # Allow a context switch here so that other workers can get_nowait and realize the queue is only 1 elem
                await asyncio.sleep(.1)

                if download.start_time is None:
                    download.start_time = datetime.datetime.now()

                finished = self.torrent_downloader.is_transfer_finished(download.transfer, download.start_time)
                if finished:
                    insert_again = await self._worker_handle_download(download)
                elif finished is None:
                    insert_again = await self._worker_handle_transfer_progress(download)
                else:
                    insert_again = False

                if insert_again:
                    self.downloads_queue.put_nowait(download)

                self.downloads_queue.task_done()

                await asyncio.sleep(5)

        except asyncio.QueueEmpty:
            logging.debug('Downloads_Queue is empty, work is finished.')
        except RuntimeError:
            logging.debug('Worker was being cancelled.')
        except Exception as e:
            logging.error('Worker got exception: "{}"'.format(repr(e)))

    @staticmethod
    async def _worker_handle_transfer_progress(download):
        if download.transfer is None:
            logging.warning('Warning torrenting {}: Torrent not found anymore!'.format(
                download.information.show.name))
            # Reinsert download $retries times, as premiumize.me forgets the transfer between "finished" and "ready"
            if download.retries > 0:
                download.retries -= 1
                return True
        elif download.transfer.is_running():
            logging.debug('{} {}: {}'.format(download.information.show.name, download.reference,
                                             download.transfer.status_msg()))
            return True
        else:
            logging.error('Error torrenting {}: {}'.format(download.transfer.name,
                                                           download.transfer.message))

    async def _worker_handle_download(self, download):
        success_file = await self._download(download)
        if success_file:
            await self._cleanup(download, success_file)
            logging.debug('Finished downloading {} {}'.format(download.information.show.name,
                                                              download.reference))
        elif download.retries > 0:
            download.retries -= 1
            return True
        else:
            logging.error('Download {} {} was not downloadeable.'.format(download.information.show.name,
                                                                         download.reference))

    @staticmethod
    async def _download(download):
        season_ = download.reference if type(download.reference) == Season else \
            download.information.show.seasons.get(download.reference.season)
        season_directory = os.path.join(download.information.download_directory,
                                        str(download.information.show.get_storage_name()),
                                        str(season_))

        os.makedirs(season_directory, exist_ok=True)

        file_ = await download.downloader.get_file_from_transfer(download.transfer)
        if file_:
            success = await download.downloader.download_file(file_, season_directory)
            if success:
                return file_

    @staticmethod
    async def _cleanup(download, file_):
        logging.info('Cleaning up {}'.format(file_.name))
        await download.downloader.delete(file_)

    def __bool__(self):
        return bool(self.torrent_downloader)
