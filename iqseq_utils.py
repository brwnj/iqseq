#!/usr/bin/env python
# encoding: utf-8

import sys
import numpy as np
import pandas as pd
from toolshed import nopen, reader
from Bio import trie, triefind
from collections import Counter
from itertools import islice, ifilterfalse

def read_fastq(fh):
    """FASTQ parser that yields name, seq, and qual."""
    while True:
        values = list(islice(fh, 4))
        if len(values) == 4:
            id1, seq, id2, qual = values
        elif len(values) == 0:
            raise StopIteration
        else:
            raise EOFError("unexpected end of file")
        assert id1.startswith('@')
        assert id2.startswith('+')
        assert len(seq) == len(qual)
        yield id1[1:-1], seq[:-1], qual[:-1]

def trim_seq(seq, base=5):
    """round length of sequence to nearest `base`"""
    return seq[:int(base * round(len(seq)/base))]

def process_exact_fastq(fastq, n):
    """Group identical reads using a Counter. Returns Counter."""
    c = Counter()
    with nopen(fastq) as fh:
        for name, seq, qual in read_fastq(fh):
            seq = trim_seq(seq, 4)
            if len(seq) < n: continue
            c.update([seq])
    return c

def process_exact_txt(files, cutoff):
    """returns Counter from multiple quantify runs"""
    c = Counter()
    for f in files:
        for l in reader(f, header=['seq','count']):
            if int(l['count']) < cutoff: continue
            c.update([l['seq']])
    return c

def process_counted(fp, cutoff):
    c = Counter()
    for l in reader(fp, header=['seq','count']):
        count = int(l['count'])
        if count < cutoff: continue
        c[l['seq']] = count
    return c

def get_seq_bins(fp):
    """fp to text. no ints on this input."""
    c = Counter()
    for l in nopen(fp):
        c[l.strip()] = 0
    return c

def chunker(it, n):
    # chunker('AAAABBBC', 4) --> AAAA AAAB AABB ABBB BBBC
    return [it[i:i+n] for i in xrange(0, len(it)+1-n, 1)]

def construct_simple_trie(counter):
    t = trie.trie()
    for seq, count in counter.iteritems():
        t[seq] = count
    return t

def construct_complex_trie(counter, lengths=None):
    t = trie.trie()
    seqs = list(counter)
    seqs.sort(key=len, reverse=True)
    if lengths is None:
        lengths = sorted(set([len(k) for k in seqs]))
    for seq in seqs:
        seq_len = len(seq)
        for l in lengths:
            if l > seq_len: continue
            for subseq in chunker(seq, l):
                if t.has_key(subseq): continue
                if subseq == seq:
                    t[seq] = counter[seq]
                else:
                    t[subseq] = seq
    return t

def process_exact_substring(counter, t):
    """use triefind.find to gather identical substring matches"""
    seqs = list(counter)
    seqs.sort(key=len, reverse=True)
    for seq in seqs:
        l = len(seq)
        for (match, start, end) in triefind.find(seq, t):
            if len(match) == l: continue
            counter[seq] += counter[match]
            counter[match] = 0
    counter += Counter()
    return counter

def unique_everseen(iterable, key=None):
    seen = set()
    seen_add = seen.add
    if key is None:
        for element in ifilterfalse(seen.__contains__, iterable):
            seen_add(element)
            yield element
    else:
        for element in iterable:
            k = key(element)
            if k not in seen:
                seen_add(k)
                yield element

def process_similar(counter, t, n):
    """trie is composed of sequences being compared."""
    seqs = list(counter)
    seqs.sort(key=len, reverse=True)
    lengths = sorted(set([len(k) for k in seqs]))
    progress = 100
    to_process = len(seqs)
    for i, seq in enumerate(seqs, start=1):
        if i % progress == 0:
            progress = int(progress * 1.5)
            print >>sys.stderr, "processed %d of %d" % (i, to_process)
        if counter[seq] == 0: continue
        for (k, v, dist) in unique_everseen(t.get_approximate(seq, n), lambda (m,c,d): m):
            if dist == 0 or k == seq: continue
            if type(v) is int:
                counter[seq] += counter[k]
                counter[k] = 0
            else:
                # k is a subsequence; therefore add seq to v
                counter[v] += counter[seq]
                counter[seq] = 0
    counter += Counter()
    return counter

def process_similar_matrix(bins, seqs, t, n):
    """
    bins - sequence bins
    seqs - sequences to bin
    t    - trie
    n    - mismatches
    
    returns Counter
    """
    sample_seqs = list(seqs)
    sample_seqs.sort(key=len, reverse=True)
    to_process = len(sample_seqs)
    progress = 100
    
    for i, seq in enumerate(sample_seqs, start=1):
        
        if i % progress == 0:
            progress = int(progress * 1.5)
            print >>sys.stderr, "    >> processed {i} of {to_process}".format(**locals())
        
        # returning bins to which the sequence belongs
        for (k, v, dist) in unique_everseen(t.get_approximate(seq, n), lambda (m,c,d): m):

            if type(v) is int:
                bins[k] += seqs[seq]
                # set to zero? avoids adding counts to multiple bins
                seqs[seq] = 0

            else:
                bins[v] += seqs[seq]
                seqs[seq] = 0

    return bins

def scalefactor(counts):
    # mask inf and nan
    ma = np.ma.masked_invalid(counts)
    return np.exp(np.ma.median(ma))

def write_table(d, norm=False):
    if norm:
        df = pd.DataFrame(d)
        # log of counts
        lg = df.apply(np.log)
        # per sample: exponential(median(log(counts) - geometric mean))
        sf = lg.sub(lg.mean(axis=1), axis=0).apply(scalefactor, axis=0)
        # apply scaling
        df = df.div(sf, axis=1)
    else:
        df = pd.DataFrame(d)
    df.to_csv(sys.stdout, sep="\t")
