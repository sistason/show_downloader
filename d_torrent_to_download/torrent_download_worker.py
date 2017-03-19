import asyncio
import logging
import os
from multiprocessing import Process


class TorrentDownloadWorker(Process):
    def __init__(self, downloads, shutdown, transfers, event_loop):
        super().__init__()
        self.downloads_queue = downloads
        self.shutdown = shutdown
        self.transfers = transfers
        self.event_loop = event_loop

    def run(self):
        while not self.shutdown or self.downloads_queue.empty():
            download = self.downloads_queue.get()
            if self._is_transfer_ready_to_download(download):
                self._download(download)
                self._cleanup(download)

    def _is_transfer_ready_to_download(self, download):
        download.transfer = self._get_torrent_transfer(download.upload)
        if download.transfer is None:
            logging.error('Error torrenting {}, torrent not found anymore!'.format(download.name))
            return False

        if download.transfer.is_running():
            self.downloads_queue.put(download)
            return False

        if download.transfer.status == 'error':
            logging.error('Error torrenting {}: {}'.format(download.transfer.name, download.transfer.message))
            return False

        return True

    def _get_torrent_transfer(self, upload):
        for transfer in self.transfers:
            if transfer.id == upload.id:
                return transfer

    def _download(self, download):
        episode_directory = os.path.join(download.download_directory, str(download.show.name),
                                         str(download.show.seasons.get(download.episode.season)))
        os.makedirs(episode_directory, exist_ok=True)

        future = asyncio.ensure_future(download.downloader.download_file(download.transfer,
                                                                             download_directory=episode_directory))
        self.event_loop.run_until_complete(future)
        return future.result()

    def _cleanup(self, download):
        logging.info('Cleaning up {}'.format(download.upload.name))
        self.event_loop.run_until_complete(download.downloader.delete(download.upload))