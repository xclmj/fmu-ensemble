# -*- coding: utf-8 -*-
"""Implementation of realization classes

A realization is a set of results from one subsurface model
realization. A realization can be either defined from
its output files from the FMU run on the file system,
it can be computed from other realizations, or it can be
an archived realization.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import re
import copy
import glob
import json
import numpy
import pandas as pd

import ert.ecl
from fmu import config

from .virtualrealization import VirtualRealization

fmux = config.etc.Interaction()
logger = fmux.basiclogger(__name__)

if not fmux.testsetup():
    raise SystemExit()


class ScratchRealization(object):
    r"""A representation of results still present on disk

    ScratchRealization's point to the filesystem for their
    contents.

    A realization must at least contain a STATUS file.
    Additionally, jobs.json and parameters.txt will be attempted
    loaded by default.

    The realization is defined by the pointers to the filesystem.
    When asked for, this object will return data from the
    filesystem (or from cache if already computed).

    The files dataframe is the central filesystem pointer
    repository for the object. It will at least contain
    the columns
    * FULLPATH absolute path to a file
    * FILETYPE filename extension (after last dot)
    * LOCALPATH relative filename inside realization diretory
    * BASENAME filename only. No path. Includes extension

    This dataframe is available as a read-only property from the object

    Args:
        path (str): absolute or relative path to a directory
            containing a realizations files.
        realidxregexp: a compiled regular expression which
            is used to determine the realization index (integer)
            from the path. First match is the index.
            Default: realization-(\d+)
    """
    def __init__(self, path,
                 realidxregexp=re.compile(r'.*realization-(\d+)')):
        self._origpath = path

        self.files = pd.DataFrame(columns=['FULLPATH', 'FILETYPE',
                                           'LOCALPATH', 'BASENAME'])
        self._eclsum = None  # Placeholder for caching

        # The datastore for internalized data. Dictionary
        # indexed by filenames (local to the realization).
        # values in the dictionary can be either dicts or dataframes
        self.data = {}

        abspath = os.path.abspath(path)
        realidxmatch = re.match(realidxregexp, abspath)
        if realidxmatch:
            self.index = int(realidxmatch.group(1))
        else:
            logger.warn('Realization %s not valid, skipping',
                        abspath)

        # Now look for a minimal subset of files
        if os.path.exists(os.path.join(abspath, 'STATUS')):
            filerow = {'LOCALPATH': 'STATUS',
                       'FILETYPE': 'STATUS',
                       'FULLPATH': os.path.join(abspath, 'STATUS'),
                       'BASENAME': 'STATUS'}
            self.files = self.files.append(filerow, ignore_index=True)
        else:
            logger.warn("Invalid realization, no STATUS file, %s",
                        abspath)
            raise IOError('STATUS file missing')

        if os.path.exists(os.path.join(abspath, 'jobs.json')):
            filerow = {'LOCALPATH': 'jobs.json',
                       'FILETYPE': 'json',
                       'FULLPATH': os.path.join(abspath, 'jobs.json'),
                       'BASENAME': 'jobs.json'}
            self.files = self.files.append(filerow, ignore_index=True)

        if os.path.exists(os.path.join(abspath, 'OK')):
            filerow = {'LOCALPATH': 'OK',
                       'FILETYPE': 'OK',
                       'FULLPATH': os.path.join(abspath, 'OK'),
                       'BASENAME': 'OK'}
            self.files = self.files.append(filerow, ignore_index=True)

        self.from_txt('parameters.txt')
        self.from_status()

    def to_virtual(self, name=None, deepcopy=True):
        """Convert the current ScratchRealization object
        to a VirtualRealization

        Args:
            description: string, used as label
            deepcopy: boolean. Set to true if you want to continue
               to manipulate the ScratchRealization object
               afterwards without affecting the virtual realization.
               Defaults to True. False will give faster execution.
        """
        if not name:
            name = self._origpath
        if deepcopy:
            return VirtualRealization(name, copy.deepcopy(self.data))
        else:
            return VirtualRealization(name, self.data)

    def from_file(self, localpath, fformat, convert_numeric=True,
                  force_reread=False):
        """
        Parse and internalize files from disk.

        Several file formats are supported:
        * txt (one key-value pair pr. line)
        * csv
        """
        if fformat == 'txt':
            self.from_txt(localpath, convert_numeric, force_reread)
        elif fformat == 'csv':
            self.from_csv(localpath, convert_numeric, force_reread)
        else:
            raise ValueError("Unsupported file format %s" % fformat)

    def from_txt(self, localpath, convert_numeric=True,
                 force_reread=False):
        """Parse a txt file with
        <key> <value>
        in each line.

        The txt file will be internalized in a dict and will be
        stored if the object is archived. Recommended file
        extension is 'txt'.

        Common usage is internalization of parameters.txt which
        happens by default, but this can be used for all txt files.

        The parsed data is returned as a dict. At the ensemble level
        the same function returns a dataframe.

        There is no get'er for the constructed data, access the
        class variable keyvaluedata directly, or rerun this function.
        (except for parameters.txt, for which there is a property
        called 'parameters')

        Args:
            localpath: path local the realization to the txt file
            convert_numeric: defaults to True, will try to parse
                all values as integers, if not, then floats, and
                strings as the last resort.
            force_reread: Force reread from file system. If
                False, repeated calls to this function will
                returned cached results.

        Returns:
            dict with the parsed values. Values will be returned as
                integers, floats or strings. If convert_numeric
                is False, all values are strings.
        """
        fullpath = os.path.join(self._origpath, localpath)
        if not os.path.exists(fullpath):
            raise IOError("File not found: " + fullpath)
        else:
            if fullpath in self.files['FULLPATH'].values and not force_reread:
                # Return cached version
                return self.data[localpath]
            elif fullpath not in self.files['FULLPATH'].values:
                filerow = {'LOCALPATH': localpath,
                           'FILETYPE': localpath.split('.')[-1],
                           'FULLPATH': fullpath,
                           'BASENAME': os.path.split(localpath)[-1]}
                self.files = self.files.append(filerow, ignore_index=True)
            try:
                keyvalues = pd.read_table(fullpath, sep=r'\s+',
                                          index_col=0, dtype=str,
                                          header=None)[1].to_dict()
            except pd.errors.EmptyDataError:
                keyvalues = {}
            if convert_numeric:
                for key in keyvalues:
                    keyvalues[key] = parse_number(keyvalues[key])
            self.data[localpath] = keyvalues
            return keyvalues

    def from_csv(self, localpath, convert_numeric=True,
                 force_reread=False):
        """Parse a CSV file as a DataFrame

        Data will be stored as a DataFrame for later
        access or storage.

        Filename is relative to realization root.

        Args:
            localpath: path local the realization to the txt file
            convert_numeric: defaults to True, will try to parse
                all values as integers, if not, then floats, and
                strings as the last resort.
            force_reread: Force reread from file system. If
                False, repeated calls to this function will
                returned cached results.

        Returns:
            dataframe: The CSV file loaded. Empty dataframe
                if file is not present.
        """
        fullpath = os.path.join(self._origpath, localpath)
        if not os.path.exists(fullpath):
            raise IOError("File not found: " + fullpath)
        else:
            # Look for cached version
            if localpath in self.data and not force_reread:
                return self.data[localpath]
            # Check the file store, append if not there
            if localpath not in self.files['LOCALPATH'].values:
                filerow = {'LOCALPATH': localpath,
                           'FILETYPE': localpath.split('.')[-1],
                           'FULLPATH': fullpath,
                           'BASENAME': os.path.split(localpath)[-1]}
                self.files = self.files.append(filerow, ignore_index=True)
            try:
                if convert_numeric:
                    # Trust that Pandas will determine sensible datatypes
                    # faster than the convert_numeric() function
                    dtype = None
                else:
                    dtype = str
                dframe = pd.read_csv(fullpath, dtype=dtype)
            except pd.errors.EmptyDataError:
                dframe = None  # or empty dataframe?

            # Store parsed data:
            self.data[localpath] = dframe
            return dframe

    def from_status(self):
        """Collects the contents of the STATUS files and return
        as a dataframe, with information from jobs.json added if
        available.

        Each row in the dataframe is a finished FORWARD_MODEL
        The STATUS files are parsed and information is extracted.
        Job duration is calculated, but jobs above 24 hours
        get incorrect durations.

        Returns:
            A dataframe with information from the STATUS files.
            Each row represents one job in one of the realizations.
        """
        from datetime import datetime, date, time
        statusfile = os.path.join(self._origpath, 'STATUS')
        if not os.path.exists(statusfile):
            # This should not happen as long as __init__ requires STATUS
            # to be present.
            return pd.DataFrame()  # will be empty
        status = pd.read_table(statusfile, sep=r'\s+', skiprows=1,
                               header=None,
                               names=['FORWARD_MODEL', 'colon',
                                      'STARTTIME', 'dots', 'ENDTIME'],
                               engine='python',
                               error_bad_lines=False,
                               warn_bad_lines=True)
        # Delete potential unwanted row
        status = status[~ ((status.FORWARD_MODEL == 'LSF') &
                           (status.colon == 'JOBID:'))]
        status.reset_index(inplace=True)
        del status['colon']
        del status['dots']
        # Index the jobs, this makes it possible to match with jobs.json:
        status.insert(0, 'JOBINDEX', status.index.astype(int))
        del status['index']
        # Calculate duration. Only Python 3.6 has time.fromisoformat().
        # Warning: Unpandaic code..
        durations = []
        for _, jobrow in status.iterrows():
            if not jobrow['ENDTIME']:  # A job that is not finished.
                durations.append(numpy.nan)
            else:
                hms = map(int, jobrow['STARTTIME'].split(':'))
                start = datetime.combine(date.today(),
                                         time(hour=hms[0], minute=hms[1],
                                              second=hms[2]))
                hms = map(int, jobrow['ENDTIME'].split(':'))
                end = datetime.combine(date.today(),
                                       time(hour=hms[0], minute=hms[1],
                                            second=hms[2]))
                # This works also when we have crossed 00:00:00.
                # Jobs > 24 h will be wrong.
                durations.append((end - start).seconds)
        status['DURATION'] = durations

        # Augment data from jobs.json if that file is available:
        jsonfilename = os.path.join(self._origpath, 'jobs.json')
        if jsonfilename and os.path.exists(jsonfilename):
            try:
                jobsinfo = json.load(open(jsonfilename))
                jobsinfodf = pd.DataFrame(jobsinfo['jobList'])
                jobsinfodf['JOBINDEX'] = jobsinfodf.index.astype(int)
                # Outer merge means that we will also have jobs from
                # jobs.json that has not started (failed or perhaps
                # the jobs are still running on the cluster)
                status = status.merge(jobsinfodf, how='outer',
                                      on='JOBINDEX')
            except ValueError:
                logger.warn("Parsing file %s failed, skipping",
                            jsonfilename)
        status.sort_values(['JOBINDEX'], ascending=True,
                           inplace=True)
        self.data['STATUS'] = status
        return status

    def __getitem__(self, localpath):
        """Direct access to the realization data structure

        Calls get_df(localpath).
        """
        return self.get_df(localpath)

    def __delitem__(self, localpath):
        """Deletes components in the internal datastore.

        Silently ignores data that is not found.

        Args:
            localpath: string, fully qualified name of key
                (no shorthand as for get_df())
        """
        if localpath in self.keys():
            del self.data[localpath]

    def keys(self):
        """Access the keys of the internal data structure
        """
        return self.data.keys()

    def get_df(self, localpath):
        """Access the internal datastore which contains dataframes or dicts

        Shorthand is allowed, if the fully qualified localpath is
            'share/results/volumes/simulator_volume_fipnum.csv'
        then you can also get this dataframe returned with these alternatives:
         * simulator_volume_fipnum
         * simulator_volume_fipnum.csv
         * share/results/volumes/simulator_volume_fipnum

        but only as long as there is no ambiguity. In case of ambiguity, a
        ValueError will be raised.

        Args:
            localpath: the idenfier of the data requested

        Returns:
            dataframe or dictionary
        """
        if localpath in self.data.keys():
            return self.data[localpath]

        # Allow shorthand, but check ambiguity
        basenames = map(os.path.basename, self.data.keys())
        if basenames.count(localpath) == 1:
            shortcut2path = {os.path.basename(x): x for x in self.data}
            return self.data[shortcut2path[localpath]]
        noexts = [''.join(x.split('.')[:-1]) for x in self.data]
        if noexts.count(localpath) == 1:
            shortcut2path = {''.join(x.split('.')[:-1]): x
                             for x in self.data}
            return self.data[shortcut2path[localpath]]
        basenamenoexts = [''.join(os.path.basename(x).split('.')[:-1])
                          for x in self.data]
        if basenamenoexts.count(localpath) == 1:
            shortcut2path = {''.join(os.path.basename(x).split('.')[:-1]): x
                             for x in self.data}
            return self.data[shortcut2path[localpath]]
        raise ValueError(localpath)

    def find_files(self, paths, metadata=None):
        """Discover realization files. The files dataframe
        will be updated.

        Certain functionality requires up-front file discovery,
        e.g. ensemble archiving and ensemble arithmetic.

        CSV files for single use does not have to be discovered.

        Args:
            paths: str or list of str with filenames (will be globbed)
                that are relative to the realization directory.
            metadata: dict with metadata to assign for the discovered
                files. The keys will be columns, and its values will be
                assigned as column values for the discovered files.
        """
        if isinstance(paths, str):
            paths = [paths]
        for searchpath in paths:
            globs = glob.glob(os.path.join(self._origpath, searchpath))
            for match in globs:
                filerow = {'LOCALPATH': os.path.relpath(match, self._origpath),
                           'FILETYPE': match.split('.')[-1],
                           'FULLPATH': match,
                           'BASENAME': os.path.basename(match)}
                # Delete this row if it already exists, determined by FULLPATH
                if match in self.files.FULLPATH.values:
                    self.files = self.files[self.files.FULLPATH != match]
                if metadata:
                    filerow.update(metadata)
                # Issue: Solve when file is discovered multiple times.
                self.files = self.files.append(filerow, ignore_index=True)

    @property
    def parameters(self):
        """Access the data obtained from parameters.txt

        Returns:
            dict with data from parameters.txt
        """
        return self.data['parameters.txt']

    def get_eclsum(self):
        """
        Fetch the Eclipse Summary file from the realization
        and return as a libecl EclSum object

        Unless the UNSMRY file has been discovered, it will
        pick the file from the glob eclipse/model/*UNSMRY

        Warning: If you have multiple UNSMRY files and have not
        performed explicit discovery, this function will
        not help you (yet).

        Returns:
           EclSum: object representing the summary file. None if
               nothing was found.
        """
        if self._eclsum:  # Return cached object if available
            return self._eclsum

        unsmry_file_row = self.files[self.files.FILETYPE == 'UNSMRY']
        unsmry_filename = None
        if len(unsmry_file_row) == 1:
            unsmry_filename = unsmry_file_row.FULLPATH.values[0]
        else:
            unsmry_fileguess = os.path.join(self._origpath, 'eclipse/model',
                                            '*.UNSMRY')
            unsmry_filenamelist = glob.glob(unsmry_fileguess)
            if not unsmry_filenamelist:
                return None  # No filename matches
            unsmry_filename = unsmry_filenamelist[0]
        if not os.path.exists(unsmry_filename):
            return None
        try:
            eclsum = ert.ecl.EclSum(unsmry_filename)
        except IOError:
            # This can happen if there is something wrong with the file
            # or if SMSPEC is missing.
            logger.warning('Failed to create summary instance from %s',
                           unsmry_filename)
            return None
        # Cache result
        self._eclsum = eclsum
        return self._eclsum

    def from_smry(self, time_index='raw', column_keys=None):
        """Produce dataframe from Summary data from the realization

        When this function is called, the dataframe will be cached.
        Caching supports different time_index, but there is no handling
        of multiple sets of column_keys. The cached data will be called

          'share/results/tables/unsmry-<time_index>.csv'

        where <time_index> is among 'yearly', 'monthly', 'daily', 'last' or
        'raw' (meaning the raw dates in the SMRY file), depending
        on the chosen time_index. If a custom time_index (list
        of datetime) was supplied, <time_index> will be called 'custom'.

        Wraps ert.ecl.EclSum.pandas_frame()

        Args:
            time_index: string indicating a resampling frequency,
               'yearly', 'monthly', 'daily', 'last' or 'raw', the latter will
               return the simulated report steps (also default).
               If a list of DateTime is supplied, data will be resampled
               to these.
            column_keys: list of column key wildcards.

        Returns:
            DataFrame with summary keys as columns and dates as indices.
                Empty dataframe if no summary is available.
        """
        if not self.get_eclsum():
            # Return empty, but do not store the empty dataframe in self.data
            return pd.DataFrame()

        time_index_path = time_index
        if time_index == 'raw':
            time_index_arg = None
        elif isinstance(time_index, str):
            time_index_arg = self.get_smry_dates(freq=time_index)
        if isinstance(time_index, list):
            time_index_arg = time_index
            time_index_path = 'custom'
        # Do the actual work:
        dframe = self.get_eclsum().pandas_frame(time_index_arg, column_keys)
        dframe = dframe.reset_index()
        dframe.rename(columns={'index': 'DATE'}, inplace=True)

        # Cache the result:
        localpath = 'share/results/tables/unsmry-' +\
                    time_index_path + '.csv'
        self.data[localpath] = dframe
        return dframe

    def get_smryvalues(self, props_wildcard=None):
        """
        Fetch selected vectors from Eclipse Summary data.

        Args:
            props_wildcard : string or list of strings with vector
                wildcards
        Returns:
            a dataframe with values. Raw times from UNSMRY.
            Empty dataframe if no summary file data available
        """
        if not self._eclsum:  # check if it is cached
            self.get_eclsum()

        if not self._eclsum:
            return pd.DataFrame()

        if not props_wildcard:
            props_wildcard = [None]
        if isinstance(props_wildcard, str):
            props_wildcard = [props_wildcard]
        props = set()
        for prop in props_wildcard:
            props = props.union(set(self._eclsum.keys(prop)))
        if 'numpy_vector' in dir(self._eclsum):
            data = {prop: self._eclsum.numpy_vector(prop, report_only=False)
                    for prop in props}
        else:  # get_values() is deprecated in newer libecl
            data = {prop: self._eclsum.get_values(prop, report_only=False) for
                    prop in props}
        dates = self._eclsum.get_dates(report_only=False)
        return pd.DataFrame(data=data, index=dates)

    def get_smry_dates(self, freq='monthly'):
        """Return list of datetimes available in the realization

        Args:
        freq: string denoting requested frequency for
            the returned list of datetime. 'report' will
            yield the sorted union of all valid timesteps for
            all realizations. Other valid options are
            'daily', 'monthly' and 'yearly'.
            'last' will give out the last date (maximum),
            as a list with one element.
        Returns:
            list of datetimes. None if no summary data is available.
        """
        if not self.get_eclsum():
            return None
        if freq == 'raw':
            return self.get_eclsum().dates
        elif freq == 'last':
            return [self.get_eclsum().end_date]
        else:
            start_date = self.get_eclsum().start_date
            end_date = self.get_eclsum().end_date
            pd_freq_mnenomics = {'monthly': 'MS',
                                 'yearly': 'YS',
                                 'daily': 'D'}
            if freq not in pd_freq_mnenomics:
                raise ValueError('Requested frequency %s not supported' % freq)
            datetimes = pd.date_range(start_date, end_date,
                                      freq=pd_freq_mnenomics[freq])
            # Convert from Pandas' datetime64 to datetime.date:
            return [x.date() for x in datetimes]

    def __repr__(self):
        """Represent the realization. Show only the last part of the path"""
        pathsummary = self._origpath[-50:]
        return "<Realization, index={}, path=...{}>".format(self.index,
                                                            pathsummary)

    def get_ok(self):
        """Tell if the realization has an OK file

        This file is written by ERT when all FORWARD_MODELs
        have completed successfully"""
        okfile = os.path.join(self._origpath, 'OK')
        return os.path.exists(okfile)


def parse_number(value):
    """Try to parse the string first as an integer, then as float,
    if both fails, return the original string.

    Caveats: Know your Python numbers:
    https://stackoverflow.com/questions/379906/how-do-i-parse-a-string-to-a-float-or-int-in-python

    Beware, this is a minefield.

    Returns:
        int, float or string
    """
    if isinstance(value, int):
        return value
    elif isinstance(value, float):
        # int(afloat) fails on some, e.g. NaN
        try:
            if int(value) == value:
                return int(value)
            return value
        except ValueError:
            return value  # return float
    else:  # noqa
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value
