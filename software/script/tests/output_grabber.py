import os
import sys
import tempfile
from contextlib import ExitStack


class OutputGrabber:
    """Capture a file-backed text stream without pipe-buffer deadlocks."""

    def __init__(self, stream=None, threaded=False):
        # ``threaded`` is kept for compatibility with the previous helper.
        self.threaded = threaded
        self.origstream = sys.stdout if stream is None else stream
        self.origstreamfd = self.origstream.fileno()
        self.captured_text = ""
        self._saved_fd = None
        self._capture_file = None
        self._resources = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False

    def start(self):
        """
        Start capturing the stream data.
        """
        if self._capture_file is not None:
            raise RuntimeError("Output capture is already active")

        self.origstream.flush()
        self.captured_text = ""
        self._saved_fd = os.dup(self.origstreamfd)
        self._resources = ExitStack()
        try:
            self._capture_file = self._resources.enter_context(
                tempfile.TemporaryFile(mode="w+b")
            )
            os.dup2(self._capture_file.fileno(), self.origstreamfd)
        except OSError:
            os.close(self._saved_fd)
            self._saved_fd = None
            self._capture_file = None
            self._resources.close()
            self._resources = None
            raise

    def stop(self):
        """
        Stop capturing the stream data and save the text in `captured_text`.
        """
        if self._capture_file is None or self._saved_fd is None:
            return

        self.origstream.flush()
        os.dup2(self._saved_fd, self.origstreamfd)
        os.close(self._saved_fd)
        self._saved_fd = None

        self._capture_file.seek(0)
        encoding = self.origstream.encoding or "utf-8"
        self.captured_text = self._capture_file.read().decode(
            encoding, errors="replace"
        )
        self._capture_file = None
        self._resources.close()
        self._resources = None
