"""Imported only so the packaged .exe includes these modules for packages to use.

Packages run inside TENET Hub's own Python, so they can only import what the .exe contains:
the standard-library modules below, Pillow, numpy, soundcard, the Windows media-controls
(winrt) modules and the hub API itself.
"""
# fmt: off
import asyncio, base64, bisect, calendar, collections, colorsys, csv, dataclasses, datetime, enum  # noqa
import fractions, functools, glob, hashlib, heapq, hmac, html, html.parser, http.client  # noqa
import io, itertools, json, locale, math, operator, pathlib, queue, random, re, secrets  # noqa
import shutil, socket, sqlite3, ssl, statistics, string, struct, subprocess, tempfile, textwrap  # noqa
import threading, time, typing, unicodedata, urllib.parse, urllib.request, uuid, xml.etree.ElementTree  # noqa
import zipfile, zoneinfo  # noqa
try:
    import numpy  # noqa
except ImportError:
    pass
