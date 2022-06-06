# The MIT License (MIT)
# Copyright (c) 2021-present nextcore developers
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator
from .base import BaseGlobalRateLimiter
from .priority_request_container import PriorityRequestContainer
from asyncio import Future, get_running_loop
from logging import getLogger
from queue import PriorityQueue

if TYPE_CHECKING:
    from typing import Final

__all__: Final[tuple[str, ...]] = ("LimitedGlobalRateLimiter",)

logger = getLogger(__name__)

class LimitedGlobalRateLimiter(BaseGlobalRateLimiter):
    """A limited global rate-limiter.

    Parameters
    ----------
    limit:
        The amount of requests that can be made per second.
    """
    def __init__(self, limit: int = 50) -> None:
        self.limit: int = limit
        self.remaining: int = 50
        self._pending_requests: PriorityQueue[PriorityRequestContainer] = PriorityQueue()
        self._reserved_requests: int = 0
        self._pending_reset: bool = False

    @asynccontextmanager
    async def acquire(self, *, priority: int = 0) -> AsyncIterator[None]:
        calculated_remaining = self.remaining - self._reserved_requests
        logger.debug("Calculated remaining: %s", calculated_remaining)
        logger.debug("Reserved requests: %s", self._reserved_requests)

        if calculated_remaining == 0:
            future: Future[None] = Future()
            item = PriorityRequestContainer(priority, future)

            self._pending_requests.put_nowait(item)
            
            logger.debug("Added request to queue with priority %s", priority)
            await future
            logger.debug("Out of queue, doing request")
        
        self._reserved_requests += 1
        try:
            yield None
        finally:
            # Start a reset task
            if not self._pending_reset:
                self._pending_reset = True
                loop = get_running_loop()
                loop.call_later(1, self._reset)

            self._reserved_requests -= 1
            self.remaining -= 1

    def _reset(self) -> None:
        self._pending_reset = False

        self.remaining = self.limit
        
        to_release = min(self._pending_requests.qsize(), self.remaining - self._reserved_requests)
        logger.debug("Releasing %s requests", to_release)
        for _ in range(to_release):
            container = self._pending_requests.get_nowait()
            future = container.future

            # Mark it as completed, good practice (and saves a bit of memory due to a infinitly expanding int)
            self._pending_requests.task_done()
            
            # Release it and allow further requests
            future.set_result(None)
        if self._pending_requests.qsize():
            self._pending_reset = True
            
            loop = get_running_loop()
            loop.call_later(1, self._reset)

    def update(self, retry_after: float) -> None:
        logger.warning("Exceeded global rate-limit! (Retry after: %s)", retry_after)
