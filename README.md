# pythonfs
A FUSE-powered filesystem representation of the python object model.

Depends on fusepy and FUSE.
```
pip install fusepy
mkdir -p /tmp/testdir
python3 pythonfs.py /tmp/testdir
```

This thing is awfully slow, but it doesn't matter as no one should ever actually use it.
If you do, ... well. You shouldn't.
