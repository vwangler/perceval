# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2017 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, 51 Franklin Street, Fifth Floor, Boston, MA 02110-1335, USA.
#
# Authors:
#     Valerio Cosentino <valcos@bitergia.com>
#

import logging
import time

import requests
import urllib3.util

from .errors import RateLimitError
from ._version import __version__

logger = logging.getLogger(__name__)


class HttpClient:
    """Abstract class for HTTP clients.

    Base class to query data sources taking care of retrying
    requests in case connection issues. If the data source
    does not send back a response after retrying a request,
    a RetryError exception is thrown.

    Sub-classes can use the methods fetch to obtain data
    from the data source.

    To track which version of the client was used during
    the fetching process, this class provides a `version`
    attribute that each client may override.

    :param base_url: base URL of the data source
    :param max_retries: number of max retries to a data source
        before raising a RetryError exception
    :param status_forcelist: list of status codes where the retry
        attempts occur
    :param default_sleep_time: default time to sleep in case
        of connection problems
    :param headers: list of session headers
    """
    version = '0.1'

    DEFAULT_SLEEP_TIME = 1

    MAX_RETRIES = 5
    MAX_RETRIES_ON_CONNECT = 5
    MAX_RETRIES_ON_READ = 5
    MAX_RETRIES_ON_REDIRECT = 5
    MAX_RETRIES_ON_STATUS = 5

    DEFAULT_METHOD_WHITELIST = False
    DEFAULT_RAISE_ON_REDIRECT = True
    DEFAULT_RAISE_ON_STATUS = True
    DEFAULT_RESPECT_RETRY_AFTER_HEADER = True

    DEFAULT_RETRY_AFTER_STATUS_CODES = [413, 429, 503]
    DEFAULT_STATUS_FORCE_LIST = [408, 423, 504]

    DEFAULT_HEADERS = {'User-Agent': 'Perceval/' + __version__}

    GET = "GET"
    POST = "POST"

    def __init__(self, base_url, max_retries=MAX_RETRIES, status_forcelist=DEFAULT_STATUS_FORCE_LIST,
                 default_sleep_time=DEFAULT_SLEEP_TIME, headers=DEFAULT_HEADERS):

        self.base_url = base_url

        self.headers = headers
        self.max_retries = max_retries
        self.max_retries_on_connect = self.MAX_RETRIES_ON_CONNECT
        self.max_retries_on_read = self.MAX_RETRIES_ON_READ
        self.max_retries_on_redirect = self.MAX_RETRIES_ON_REDIRECT
        self.max_retries_on_status = self.MAX_RETRIES_ON_STATUS
        self.status_forcelist = status_forcelist
        self.method_whitelist = self.DEFAULT_METHOD_WHITELIST
        self.raise_on_redirect = self.DEFAULT_RAISE_ON_REDIRECT
        self.raise_on_status = self.DEFAULT_RAISE_ON_STATUS
        self.respect_retry_after_header = self.DEFAULT_RESPECT_RETRY_AFTER_HEADER
        self.default_sleep_time = default_sleep_time

        self._create_http_session()

    def __del__(self):
        self._close_http_session()

    def fetch(self, url, payload=None, headers=None, method=GET, stream=False, verify=True):
        """Fetch the data from a given URL.

        :param url: link to the resource
        :param payload: payload of the request
        :param headers: headers of the request
        :param method: type of request call (GET or POST)
        :param stream: defer downloading the response body until the response content is available
        :param verify: verifying the SSL certificate

        :returns a response object
        """
        if method == self.GET:
            response = self.session.get(url, params=payload, headers=headers, stream=stream, verify=verify)
        else:
            response = self.session.post(url, data=payload, headers=headers, stream=stream, verify=verify)

        response.raise_for_status()

        return response

    def _create_http_session(self):
        """Create a http session and initialize the retry object."""

        self.session = requests.Session()

        if self.headers:
            self.session.headers.update(self.headers)

        retries = urllib3.util.Retry(total=self.max_retries,
                                     connect=self.max_retries_on_connect,
                                     read=self.max_retries_on_read,
                                     redirect=self.max_retries_on_redirect,
                                     status=self.max_retries_on_status,
                                     method_whitelist=self.method_whitelist,
                                     status_forcelist=self.status_forcelist,
                                     backoff_factor=self.default_sleep_time,
                                     raise_on_redirect=self.raise_on_redirect,
                                     raise_on_status=self.raise_on_status,
                                     respect_retry_after_header=self.respect_retry_after_header)

        self.session.mount('http://', requests.adapters.HTTPAdapter(max_retries=retries))
        self.session.mount('https://', requests.adapters.HTTPAdapter(max_retries=retries))

    def _close_http_session(self):
        """Close the http session."""

        self.session.close()


class RateLimitHandler:
    """Class to handle rate limit for HTTP clients.

    :param sleep_for_rate: sleep until rate limit is reset
    :param min_rate_to_sleep: minimun rate needed to sleep until it will be rese
    :param rate_limit_header: header to know the current rate limit
    :param rate_limit_reset_header: header to know the next rate limit reset
    """
    version = '0.1'

    MIN_RATE_LIMIT = 10
    MAX_RATE_LIMIT = 500
    RATE_LIMIT_HEADER = "X-RateLimit-Remaining"
    RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset"

    def setup_rate_limit_handler(self, sleep_for_rate=False, min_rate_to_sleep=MIN_RATE_LIMIT,
                                 rate_limit_header=RATE_LIMIT_HEADER,
                                 rate_limit_reset_header=RATE_LIMIT_RESET_HEADER):
        """Setup the rate limit handler.

        :param sleep_for_rate: sleep until rate limit is reset
        :param min_rate_to_sleep: minimun rate needed to make the fecthing process sleep
        :param rate_limit_header: header from where extract the rate limit data
        :param rate_limit_reset_header: header from where extract the rate limit reset data
        """
        self.rate_limit = None
        self.rate_limit_reset_ts = None
        self.sleep_for_rate = sleep_for_rate
        self.rate_limit_header = rate_limit_header
        self.rate_limit_reset_header = rate_limit_reset_header

        if min_rate_to_sleep > self.MAX_RATE_LIMIT:
            msg = "Minimum rate to sleep value exceeded (%d)."
            msg += "High values might cause the client to sleep forever."
            msg += "Reset to %d."
            self.min_rate_to_sleep = self.MAX_RATE_LIMIT
            logger.warning(msg, min_rate_to_sleep, self.MAX_RATE_LIMIT)
        else:
            self.min_rate_to_sleep = min_rate_to_sleep

    def sleep_for_rate_limit(self):
        """The fetching process sleeps until the rate limit is restored or
           raises a RateLimitError exception if sleep_for_rate flag is disabled.
        """
        if self.rate_limit is not None and self.rate_limit <= self.min_rate_to_sleep:
            seconds_to_reset = self.rate_limit_reset_ts - int(time.time()) + 1
            cause = "Rate limit exhausted."
            if self.sleep_for_rate:
                logger.info("%s Waiting %i secs for rate limit reset.", cause, seconds_to_reset)
                time.sleep(seconds_to_reset)
            else:
                raise RateLimitError(cause=cause, seconds_to_reset=seconds_to_reset)

    def update_rate_limit(self, response):
        """Update the rate limit and the time to reset the rate limit
           from the response headers.

        :param: response: the response object
        """
        if self.rate_limit_header in response.headers:
            self.rate_limit = int(response.headers[self.rate_limit_header])
            logger.debug("Rate limit: %s", self.rate_limit)
        else:
            self.rate_limit = None

        if self.rate_limit_reset_header in response.headers:
            self.rate_limit_reset_ts = int(response.headers[self.rate_limit_reset_header])
            logger.debug("Rate limit reset: %s", self.rate_limit_reset_ts)
        else:
            self.rate_limit_reset_ts = None
