from collections import defaultdict
from errno import EACCES, ENOENT, ENOTDIR
from fuse import FUSE, FuseOSError, Operations, LoggingMixIn
from stat import S_IFDIR, S_IFREG
from sys import argv, exit
from threading import Lock
from time import time
import functools
import logging
import os
import sys


class otype:
    DIR = S_IFDIR
    FILE = S_IFREG


class path_state():
    def __init__(self, origin, path):
        self.obj = origin
        self.path = path


class classproperty(object):
    def __init__(self, f):
        self.f = f

    def __get__(self, obj, owner):
        return self.f(owner)


class feature_manager():
    class feature():
        all = defaultdict(None)
        types = defaultdict(lambda: 0)

        @classmethod
        def dir(cls):
            return [(e.type, k) for k, e in cls.all.items()]

        @classmethod
        def add_feature(cls, ftype, name, f):
            cls.all[name] = f
            cls.types[ftype] += 1

        @classmethod
        def add(cls, feature_type):
            def wrap(f):
                @functools.wraps(f)
                def wrapped(*args, **kwargs):
                    r = f(*args, **kwargs)
                    if r:
                        return (feature_type, r)
                wrapped.type = feature_type
                cls.add_feature(feature_type, f.__name__, wrapped)
                return wrapped
            return wrap

        def fencode(f):
            @functools.wraps(f)
            def wrap(st, *args, **kwargs):
                return f(st, *args, **kwargs).encode('utf8')
            return wrap

        def head(f):
            @functools.wraps(f)
            def wrap(st, *args, **kwargs):
                if st.path:
                    print("path isn't empty despite head decorator: {}"
                          .format(str(st.path)), file=sys.stderr)
                    raise FuseOSError(ENOENT)
                return f(st, *args, **kwargs)
            return wrap

    @classproperty
    def features(cls):
        return cls.feature.all

    @feature.add(otype.DIR)
    def attr(st):
        if st.path:
            st.obj = getattr(st.obj, st.path.pop(0), None)
            if not st.obj:
                print('no such object atribute',
                      file=sys.stderr)
                raise FuseOSError(ENOENT)
            return
        return [(otype.DIR, e) for e in dir(st.obj)]

    @feature.add(otype.FILE)
    @feature.fencode
    def str(st):
        return str(st.obj)+'\n'

    @feature.head
    @feature.add(otype.FILE)
    def cls(st):
        st.path = ['attr', '__class__', 'str']


def path_of_str(s):
    is_dir = False
    r = s.split('/')
    r.pop(0)
    if not r[-1]:
        r.pop()
        is_dir = True
    return (r, is_dir)


def get_object(origin, path_str, ignore_otype=False):
    path, is_dir = path_of_str(path_str)
    state = path_state(origin, path)
    while state.path:
        feature_name = state.path.pop(0)
        print('trying to get feature `{}` with state : {}:{}'
              .format(feature_name, str(state.obj), str(state.path)))
        fct = feature_manager.features.get(feature_name, None)
        if not fct:
            print('No such feature. ({})'.format(feature_name),
                  file=sys.stderr)
            raise FuseOSError(ENOENT)
        else:
            res = fct(state)
            if res:
                t, v = res
                if not ignore_otype and is_dir and t != otype.DIR:
                    raise FuseOSError(ENOTDIR)
                return res
    return otype.DIR, feature_manager.feature.dir()


class FDPool():
    def __init__(self, maxfds=65536):
        self.pool = {}
        self.maxfds = maxfds

    def get(self):
        for i in range(self.maxfds):
            if i not in self.pool:
                self.pool[i] = None
                return i
        raise RuntimeError('No more file descriptiors to allocate')

    def release(self, fd):
        return self.pool.pop(fd)

    def setcache(self, fd, cache):
        self.pool[fd] = cache

    def clearcache(self, fd):
        self.pool[fd] = None


class PythonFS(LoggingMixIn, Operations):
    def __init__(self, obj, uid=0, gid=0):
        self.obj = obj
        self.fdpool = FDPool()
        self.uid = 0
        self.gid = 0
        self.now = time()
        self.path_fds = {}

    def get_object(self, *args, **kwargs):
        return get_object(self.obj, *args, **kwargs)

    def __call__(self, op, path, *args):
        return super().__call__(op, path, *args)

    def lolnop(*args, **kwargs):
        raise FuseOSError(EACCES)

    def placeholder(*args, **kwargs):
        pass

    flush = fsync = placeholder
    listxattr = setxattr = getxattr = None
    link = write = rmdir = utimens = unlink = truncate = rename = symlink = \
        mkdir = mknod = create = chown = chmod = statfs = readlink = lolnop

    def access(self, path, mode):
        is_file = True

        try:
            get_object(self.obj, path, True)
        except FuseOSError:
            is_file = False
        if mode != 0 and not is_file:
            raise FuseOSError(ENOENT)

        if (mode & os.W_OK):
            raise FuseOSError(EACCES)

    def getattr(self, path, fh=None):
        tpe, val = get_object(self.obj, path)
        if tpe == otype.DIR:
            links = 2 + sum(1 for e in val if e[0] == otype.DIR)
        else:
            links = 1
        return {
            'st_atime': self.now,
            'st_ctime': self.now,
            'st_mtime': self.now,
            'st_gid':   self.gid,
            'st_uid':   self.uid,
            'st_mode':  tpe | 0o555,
            'st_nlink': links,
            'st_size':  4096 if tpe == otype.DIR else len(val),
        }

    def open(self, path, flags):
        fd = self.fdpool.get()
        self.fdpool.pool[fd] = get_object(self.obj, path)
        return fd

    def read(self, path, size, offset, fh):
        return self.fdpool.pool[fh][1][offset:offset+size]

    def readdir(self, path, fh):
        ret = [e[1] for e in self.get_object(path)[1]]
        ret.extend(('.', '..'))
        return ret

    def release(self, path, fh):
        self.fdpool.release(fh)


def pythonfs(obj, path):
    return FUSE(PythonFS(obj), path, foreground=True)


if __name__ == '__main__':
    if len(argv) != 2:
        print('usage: %s <root> <mountpoint>' % argv[0],
              file=sys.stderr)
        exit(1)

    logging.basicConfig(level=logging.DEBUG)
    fuse = pythonfs([42], argv[1])
