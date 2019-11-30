"""

"aisecurity.utils.misc"

Miscellaneous tools.

"""

import functools
import os
import sys
import time

from keras import backend as K


# PRINT HANDLING
class HidePrints(object):

    def __enter__(self):
        self.to_show = sys.stdout
        sys.stdout = open(os.devnull, "w")

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout.close()
        sys.stdout = self.to_show


# TIMER
def timer(message="Time elapsed"):
    def _timer(func):
        @functools.wraps(func)
        def _func(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            print("{}: {}s".format(message, round(time.time() - start, 3)))
            return result

        return _func

    return _timer
