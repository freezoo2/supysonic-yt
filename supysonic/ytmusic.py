import os
from operator import truediv

import yt_dlp
from ytmusicapi import YTMusic
import mutagen
import logging
from collections import OrderedDict
from typing import List, Dict

from .scanner import Scanner
from .db import Folder
from concurrent.futures import ThreadPoolExecutor
from readable_number import ReadableNumber
import re
import urllib.request
import threading



#YTMusic Artist Id, browseId seem to be the same thing. get_artist doesn't return the artist browseID
# Usually starts with UC, example UCX0rR7x8-nLew2t8UH-n2aw
#YTMusic Album Id, browseId seems to be the same. get_album doesn't return the album browseID
# usually starts with MP, example: MPREb_CpQIsqSuLCJ
#YTmusic songID is videoID, can be None if song is not available
# len is shorter than Album and ArtistID (song is 12 characters), example lR4ZjMVHCuM

# FolderID for artist should be artist BrowseID
# FolderID for album should be album browseID
# Get cover for


logger = logging.getLogger(__name__)


class YTM():
    def __init__(self):
        pass

    def search(self, query: str, artist_limit: int, album_limit: int, song_limit: int):
        executor = ThreadPoolExecutor(max_workers=30)
        ytm = YTMusic()
        if artist_limit > 20:
            artist_limit = 20
        if album_limit > 20:
            album_limit = 20
        if song_limit > 50:
            song_limit = 50
        artists_search = None
        albums_search = None
        songs_search = None
        playlist_search = None
        artist_threads = {}
        playlist_limit = 0
        artists = []
        albums = []
        songs = []
        query_is_playlist_search = False

        if query.lower().startswith("playlist ") or query.lower().startswith("pl "):
            query_is_playlist_search = True
            query = " ".join(query.split(" ")[1:])
        elif query.lower().startswith("pl:"):
            query_is_playlist_search = True
            query = ":".join(query.split(":")[1:])
        if query_is_playlist_search:
            playlist_limit = album_limit
            artist_limit = 0
            album_limit = 0
            song_limit = 0
            playlist_search = executor.submit(ytm.search, query, **{"filter": "playlists"})

        if artist_limit > 0:
            artists_search = executor.submit(ytm.search, query, **{"filter": "artists"})
        if song_limit > 0:
            songs_search = executor.submit(ytm.search, query, **{"filter": "songs"})
        if album_limit > 0:
            albums_search = executor.submit(ytm.search, query, **{"filter": "albums"})

        if artists_search:
            artists_result = artists_search.result()
            for artist in artists_result:
                browseId = artist['browseId']
                if browseId in artist_threads.keys():
                    continue
                artist_threads[browseId] = executor.submit(ytm.get_artist, browseId)
        for browseId, thread in artist_threads.items():
            try:
                artist = thread.result()
            except:
                continue
            artist_item = self._format_artist_from_get_artist(artist, browseId, include_albums=False)
            if artist_item:
                artists.append(artist_item)
        if albums_search:
            for album in albums_search.result():
                album_item = self._format_album_from_search(album)
                if album_item:
                    albums.append(album_item)
        if songs_search:
            for song in songs_search.result():
                song_item = self._format_song(song)
                if song_item:
                    songs.append(song_item)
        if playlist_search:
            for playlist in playlist_search.result():
                playlist_item = self._format_playlist_as_album(playlist)
                if playlist_item:
                    albums.append(playlist_item)
        return OrderedDict(
            (
                ("artist", artists[:artist_limit]),
                ("album", albums[:album_limit + playlist_limit]),
                ("song", songs[:song_limit]),
            )
        )

    def _format_artist_from_get_artist(self, artist_dict: dict, browseId: str, include_albums: bool = True):
        views = artist_dict.get("views", "0")
        views = self._readable_number_of_views(views)
        artist_dict['name'] = f"{artist_dict.get('name')} ({views} views)"
        artist_dict['browseId'] = browseId
        artist_dict['albumCount'] = len(artist_dict.get("albums", {}).get("results", [""]))
        artist_dict['album'] = []
        if include_albums:
            artist_dict['album'] = []
            for album in artist_dict.get('albums', {}).get("results", []):
                album['artists'] = [{'name': artist_dict['name'], 'id': browseId}]
                artist_dict['album'].append(self._format_album(album))
        return self._format_artist(artist_dict)


    def _format_artist(self, artist_dict: dict):
        artist_id = artist_dict.get('browseId')
        artist_name = artist_dict.get('name')
        if not artist_id or not artist_name:
            return {}
        return self._sanitize_info(
            {"id": artist_id,
             "name": artist_name,
             "albumCount": artist_dict.get("albumCount", 1),
             "coverArt": artist_id,
             "album": artist_dict.get("album", [])
             })

    def _readable_number_of_views(self, number_of_views: str):
        if not number_of_views:
            number_of_views = "0"
        views_numbers = "".join(re.findall("[0-9]", number_of_views))
        return str(ReadableNumber(int(views_numbers), use_shortform=True, precision=2))

    def _format_album_from_search(self, album_dict: Dict):
        album_dict['tracks'] = []
        return self._format_album(album_dict)

    def _format_album_from_get_album(self, album_dict: Dict, browseId: str):
        album_dict['browseId'] = browseId
        return self._format_album(album_dict)

    def _format_album(self, album_dict):
        album_id = album_dict.get("browseId", None)
        if not album_id:
            return {}
        album_name = album_dict.get("title", "NotAvailable")
        year = album_dict.get('year', "1900")
        if not year:
            year = 1900
        songCount = len(album_dict.get('tracks', [1]))
        if songCount == 0:
            songCount = 1
        songs = []
        for song in album_dict.get("tracks", []):
            formatted_song = self._format_song_from_get_album(song, album_id, album_name)
            if not formatted_song:
                continue
            songs.append(formatted_song)
        return self._sanitize_info(
            {
                "id": album_id,
                "name": album_name,
                "coverArt": album_id,
                "artist": album_dict.get("artists", [{}])[0].get('name', "NotAvailable"),
                "artistId": album_dict.get("artists", [{}])[0].get('id', "NotAvailable"),
                "songCount": songCount,
                "duration": str(album_dict.get("duration_seconds", "100")),
                "created": f"{year}-01-01T00:00:00",
                "song": songs
            })

    def _format_playlist_as_album(self, playlist_dict: Dict):
        views = playlist_dict.get('itemCount', "0")
        browseId = playlist_dict.get("browseId", None)
        if not browseId:
            return None
        if not views:
            views = "0"
        playlist_dict['title'] = f"Playlist: {playlist_dict.get("title", "")} ({views} views)"
        playlist_dict['browseId'] = "PL_" + browseId
        playlist_dict['artists'] = [{'name': playlist_dict.get('author', 'Unknown'), 'id': "NotAvailable"}]
        return self._format_album(playlist_dict)

    def _format_song_from_get_album(self, song_dict: Dict, album_id: str, album_name):
        song_dict["album"] = {'id': album_id, 'name': album_name}
        return self._format_song(song_dict)

    def _format_song_from_get_watch_playlist(self, song_dict: Dict):
        song_dict['duration_seconds'] = self._get_seconds(song_dict.get("length", "1:00"))
        return self._format_song(song_dict)

    def _format_song(self, song_dict: Dict):
        song_id = song_dict.get('videoId', None)
        if not song_id:
            return {}
        artist = song_dict.get("artists", [{}])[0].get("name", "NotAvailable")
        album = song_dict.get("album", {}).get("name", "NotAvailable")
        title = song_dict.get("title", "NotAvailable")
        year = song_dict.get('year', "1900")
        if not year:
            year = 1900
        return self._sanitize_info(
            {
                "id": song_id,
                "parent": song_dict.get("album", {}).get("id", "NotAvailable"),
                "isDir": "false",
                "title": title,
                "album": album,
                "artist": artist,
                "track": song_dict.get("trackNumber", "0"),
                "size": "1",
                "coverArt": song_dict.get("album", {}).get("id", ""),
                "contentType": "audio/mpeg",
                "suffix": "m4a",
                "duration": str(song_dict.get("duration_seconds", 100)),
                "bitRate": "128",
                "path": f"{artist}/{album}/{title}.m4a",
                "isVideo": "false",
                "discNumber": "1",
                "created": f"{year}-01-01T00:00:00",
                "albumId": song_dict.get("album", {}).get("id", "NotAvailable"),
                "artistId": song_dict.get("artists", [{}])[0].get("id", "NotAvailable"),
                "type": "music",
            })

    def _get_seconds(self, time_str: str):
        # split in hh, mm, ss
        if len(time_str.split(":")) > 2:
            hh, mm, ss = time_str.split(':')
        else:
            mm, ss = time_str.split(':')
            hh = 0
        return int(hh) * 3600 * int(mm) * 60 + int(ss)

    def get_song_detail(self, videoId: str):
        ytm = YTMusic()
        songs = ytm.get_watch_playlist(videoId=videoId)
        song_found = None
        for song in songs.get('tracks', []):
            if song.get("videoId", "") == videoId:
                song_found = song
                break
        if not song_found:
            return {}
        return self._format_song_from_get_watch_playlist(song_found)

    def add_song(self, videoId: str):
        music_folder = Folder.get_by_id(1).path
        song_info = self.get_song_detail(videoId)
        artist = song_info.get("artist")
        album = song_info.get("album")
        song_id = song_info.get("id", "NotAvailable")
        album_id = song_info.get("albumId", "NotAvailable")
        artist_id = song_info.get("artistId", "NotAvailable")
        song_folder = f'{music_folder}/{artist}/{album}'
        song_title = song_info.get("title")
        os.makedirs(song_folder, exist_ok=True)
        filename = f'{song_folder}/{song_title}'
        # if os.path.isfile(f"{filename}.m4a"):
        #     return
        ydl_opts = {'outtmpl': {'default': filename},
                    'format': 'bestaudio/best',
                    'extractor_args': {'youtube': {'player_client': ['web']}},
                    'extract_flat': 'discard_in_playlist',
                    'ignoreerrors': 'only_download',
                    'fragment_retries': 10,
                    'postprocessors': [{'key': 'FFmpegExtractAudio',
                                        'nopostoverwrites': False,
                                        'preferredcodec': 'best',
                                        'preferredquality': '5'},
                                       {'add_infojson': 'if_exists',
                                        'add_metadata': True,
                                        'key': 'FFmpegMetadata'},
                                       ]
                    }
        yt = yt_dlp.YoutubeDL(ydl_opts)
        yt.download(videoId)
        tags = mutagen.mp4.MP4(f"{filename}.m4a")
        desired_tags = ['©nam', '©ART', '©alb', 'aART']
        for tag in list(tags.keys()):
            if tag not in desired_tags:
                print(f"removing tag {tag}")
                tags.pop(tag)
        tags['m_id'] = song_id
        tags['a_id'] = album_id
        tags['r_id'] = artist_id
        tags.save()
        scanner = Scanner()
        try:
            folder_id = Folder.get_by_id(album_id)
        except:
            folder_id = None
        if not folder_id:
            self._download_biggest_thumbnail_url(song_info.get("thumbnail_list", []), f"{song_folder}/cover.jpg")
            folder = Folder()
            folder.create(id=album_id, name=album, path=song_folder, root=0, cover_art="cover.jpg")
            scanner.add_cover(song_folder)
        scanner.scan_file(f"{filename}.m4a")

    def _download_biggest_thumbnail_url(self, thumbnail_list: List, filename: str):
        url = self._get_biggest_thumbnail_url(thumbnail_list)
        if not url:
            return False
        urllib.request.urlretrieve(url, filename)
        return True

    def _get_biggest_thumbnail_url(self, thumbnail_list: List):
        if not thumbnail_list:
            return ""
        thumbnail_list.sort(key = lambda x: x.get("height", 0), reverse=True)
        return thumbnail_list[0].get("url", "")

    def _sanitize_info(self, info: Dict):
        for key, value in info.items():
            if value == None:
                info[key] = "NotAvailable"
        return info


    def get_album(self, browseId: str):
        if browseId.startswith("PL_"):
            return self._get_playlist(browseId)
        ytm = YTMusic()
        album = ytm.get_album(browseId)
        return self._format_album_from_get_album(album, browseId)

    def _get_playlist(self, browseId: str):
        ytm = YTMusic()
        playlist = ytm.get_playlist(browseId[3:], limit=None)
        playlist['browseId'] = browseId
        return self._format_playlist_as_album(playlist)

    def get_artist(self, browseId: str):
        ytm = YTMusic()
        artist = ytm.get_artist(browseId)
        return self._format_artist_from_get_artist(artist, browseId)

    def get_artist_info(self, browseId: str):
        ytm = YTMusic()
        artist = ytm.get_artist(browseId)
        thumbnail = self._get_biggest_thumbnail_url(artist.get('thumbnails', []))
        similar_artists = []
        for similar_artist in artist.get('related', {}).get('results', []):
            similar_artists.append({'id': similar_artist.get('browseId', '00000000'), 'name': similar_artist.get('title', 'NA')})
        artist_info = {
            'biography': artist.get('description', ''),
            'musicBrainzId': '',
            'lastFmUrl': '',
            'smallImageUrl': thumbnail,
            'mediumImageUrl': thumbnail,
            'largeImageUrl': thumbnail,
            'similarArtist': similar_artists
        }
        return artist_info

    def get_top_songs(self, artist_name: str):
        ytm = YTMusic()
        songs = []
        artist_name = artist_name.lower()
        artists = ytm.search(artist_name, filter='artists')
        artist_found = False
        for artist in artists:
            if artist.get('artist', '').lower() == artist_name:
                artist_found = True
                break
        if not artist_found:
            return songs
        artist = ytm.get_artist(artist.get('browseId'))
        for song in artist.get('songs', {}).get('results', []):
            formatted_song = self._format_song(song)
            formatted_song['year'] = "1900"
            formatted_song['genre'] = "music"
            songs.append(self._format_song(song))
        return {'song': songs}

    def get_cover_art(self, eid: str):
        ytm = YTMusic()
        music_folder = Folder.get_by_id(1).path
        os.makedirs(music_folder + "/coverArts/", exist_ok=True)
        filename = music_folder + "/coverArts/" + eid + ".jpg"
        if os.path.isfile(filename):
            return filename
        thumbnail_list = []
        if len(eid) <= 12:
            # videoId (song)
            # Should only fetch album cover Art, something is weird
            if eid == "NotAvailable":
                return None
            songs = ytm.get_watch_playlist(videoId=eid)
            song_found = None
            for song in songs.get('tracks', []):
                if song.get("videoId", "") == eid:
                    song_found = song
                    break
            if not song_found:
                return None
            thumbnail_list = song_found.get("thumbnail", [])
        elif eid.startswith("MP"):
            # (browseId) Album ID
            album = ytm.get_album(eid)
            thumbnail_list = album.get("thumbnails", [])
        else:
            # Artist ID
            artist = ytm.get_artist(eid)
            thumbnail_list = artist.get("thumbnails", [])
        if not self._download_biggest_thumbnail_url(thumbnail_list, filename):
            return None
        return filename

