# This code is part of the Fred2 distribution and governed by its
# license.  Please see the LICENSE file that should have been included
# as part of this package.
"""
.. module:: EpitopePrediction.ANN
   :synopsis: This module contains all classes for ANN-based epitope prediction methods.
.. moduleauthor:: schubert, walzer

"""
import abc

import itertools
import warnings
import pandas
import subprocess
import csv
import os
import math

from collections import defaultdict

from Fred2.Core.Allele import Allele
from Fred2.Core.Peptide import Peptide
from Fred2.Core.Result import EpitopePredictionResult
from Fred2.Core.Base import AEpitopePrediction, AExternal
from tempfile import NamedTemporaryFile


class AExternalEpitopePrediction(AEpitopePrediction, AExternal):
    """
        Abstract class representing an external prediction function. Implementations shall wrap external binaries by
        following the given abstraction.
    """

    @abc.abstractmethod
    def prepare_input(self, _input, _file):
        """
        Prepares input for external tools
        and writes them to _file in the specific format

        NO return value!

        :param: list(str) _input: The peptide sequences to write into _file
        :param File _file: File-handler to input file for external tool
        """
        return NotImplementedError

    def predict(self, peptides, alleles=None, command=None, options=None, **kwargs):
        """
        Overwrites AEpitopePrediction.predict

        :param list(Peptide)/Peptide peptides: A list of or a single Peptide object
        :param list(Allele)/Allele alleles: A list of or a single Allele object. If no alleles are provided,
                                            predictions are made for all alleles supported by the prediction method
        :param str command: The path to a alternative binary (can be used if binary is not globally executable)
        :param str options: A string of additional options directly past to the external tool.
        :return: EpitopePredictionResult - A EpitopePredictionResult object
        """

        if not self.is_in_path() and command is None:
            raise RuntimeError("{name} {version} could not be found in PATH".format(name=self.name,
                                                                                    version=self.version))
        external_version = self.get_external_version(path=command)
        if self.version != external_version and external_version is not None:
            raise RuntimeError("Internal version {internal_version} does "
                               "not match external version {external_version}".format(internal_version=self.version,
                                                                                      external_version=external_version))

        if isinstance(peptides, Peptide):
            pep_seqs = {str(peptides): peptides}
        else:
            if any(not isinstance(p, Peptide) for p in peptides):
                raise ValueError("Input is not of type Protein or Peptide")
            pep_seqs = {str(p): p for p in peptides}

        if alleles is None:
            al = [Allele("HLA-" + a) for a in self.supportedAlleles]
            allales_string = {conv_a: a for conv_a, a in itertools.izip(self.convert_alleles(al), al)}
        else:
            if isinstance(alleles, Allele):
                alleles = [alleles]
            if any(not isinstance(p, Allele) for p in alleles):
                raise ValueError("Input is not of type Allele")
            allales_string = {conv_a: a for conv_a, a in itertools.izip(self.convert_alleles(alleles), alleles)}

        result = defaultdict(defaultdict)

        #group alleles in blocks of 80 alleles (NetMHC can't deal with more)
        _MAX_ALLELES = 50

        #allowe customary executable specification
        if command is not None:
            exe = self.command.split()[0]
            _command = self.command.replace(exe, command)
        else:
            _command = self.command

        allele_groups = []
        c_a = 0
        allele_group = []
        for a in allales_string.iterkeys():
            if c_a >= _MAX_ALLELES:
                c_a = 0
                allele_groups.append(allele_group)
                if str(allales_string[a]) not in self.supportedAlleles:
                    warnings.warn("Allele %s is not supported by %s"%(str(allales_string[a]), self.name))
                    allele_group = []
                    continue
                allele_group = [a]
            else:
                if str(allales_string[a]) not in self.supportedAlleles:
                    warnings.warn("Allele %s is not supported by %s"%(str(allales_string[a]), self.name))
                    continue
                allele_group.append(a)
                c_a += 1

        if len(allele_group) > 0:
                allele_groups.append(allele_group)
        #export peptides to peptide list

        for length, peps in itertools.groupby(pep_seqs.iterkeys(), key=lambda x: len(x)):
            if length < min(self.supportedLength):
                warnings.warn("Peptide length must be at least %i for %s but is %i"%(min(self.supportedLength),
                                                                                     self.name, length))
                continue
            peps = list(peps)
            tmp_out = NamedTemporaryFile(delete=False)
            tmp_file = NamedTemporaryFile(delete=False)
            self.prepare_input(peps, tmp_file)
#            tmp_file.write("\n".join(">pepe_%i\n%s"%(i, p) for i, p in enumerate(peps))
#                           if self.name.lower() in ["netmhcii","netctlpan"] else "\n".join(peps))
            tmp_file.close()

            #generate cmd command
            for allele_group in allele_groups:
                try:
                    stdo = None
                    stde = None
                    cmd = _command.format(peptides=tmp_file.name, alleles=",".join(allele_group),
                                          options="" if options is None else options, out=tmp_out.name)
                    p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    #p.wait() communicate already waits for the process https://docs.python.org/2.7/library/subprocess.html#subprocess.Popen.communicate
                    stdo, stde = p.communicate()
                    stdr = p.returncode
                    if stdr > 0:
                        raise RuntimeError("Unsuccessful execution of " + cmd + " (EXIT!=0) with error: " + stde)
                except Exception as e:
                    raise RuntimeError(e)

                res_tmp = self.parse_external_result(tmp_out.name)
                for al, ep_dict in res_tmp.iteritems():
                    for p, v in ep_dict.iteritems():
                        result[allales_string[al]][pep_seqs[p]] = v
            os.remove(tmp_file.name)
            tmp_out.close()
            os.remove(tmp_out.name)

        if not result:
            raise ValueError("No predictions could be made with " + self.name +
                             " for given input. Check your epitope length and HLA allele combination.")
        df_result = EpitopePredictionResult.from_dict(result)
        df_result.index = pandas.MultiIndex.from_tuples([tuple((i, self.name)) for i in df_result.index],
                                                        names=['Seq', 'Method'])
        return df_result


class NetMHC_3_4(AExternalEpitopePrediction):
    """
        Implements the NetMHC binding (in current form for netMHC3.4)
        Possibility could exist for function injection to support also older versions


        NetMHC-3.0: accurate web accessible predictions of human, mouse and monkey MHC class I affinities for peptides of length 8-11
        Lundegaard C, Lamberth K, Harndahl M, Buus S, Lund O, Nielsen M. Nucleic Acids Res. 1;36(Web Server issue):W509-12. 2008

        Accurate approximation method for prediction of class I MHC affinities for peptides of length 8, 10 and 11 using prediction tools trained on 9mers.
        Lundegaard C, Lund O, Nielsen M. Bioinformatics, 24(11):1397-98, 2008.

    """

    __alleles = frozenset(['A*01:01', 'A*02:01', 'A*02:02', 'A*02:03', 'A*02:06', 'A*02:11', 'A*02:12', 'A*02:16',
                           'A*02:17', 'A*02:19', 'A*02:50', 'A*03:01', 'A*11:01', 'A*23:01', 'A*24:02', 'A*24:03',
                           'A*25:01', 'A*26:01', 'A*26:02', 'A*26:03', 'A*29:02', 'A*30:01', 'A*30:02', 'A*31:01',
                           'A*32:01', 'A*32:07', 'A*32:15', 'A*33:01', 'A*66:01', 'A*68:01', 'A*68:02', 'A*68:23',
                           'A*69:01', 'A*80:01', 'B*07:02', 'B*08:01', 'B*08:02', 'B*08:03', 'B*14:02', 'B*15:01',
                           'B*15:02', 'B*15:03', 'B*15:09', 'B*15:17', 'B*18:01', 'B*27:05', 'B*27:20', 'B*35:01',
                           'B*35:03', 'B*38:01', 'B*39:01', 'B*40:01', 'B*40:02', 'B*40:13', 'B*42:01', 'B*44:02',
                           'B*44:03', 'B*45:01', 'B*46:01', 'B*48:01', 'B*51:01', 'B*53:01', 'B*54:01', 'B*57:01',
                           'B*58:01', 'B*73:01', 'B*83:01', 'C*03:03', 'C*04:01', 'C*05:01', 'C*06:02', 'C*07:01',
                           'C*07:02', 'C*08:02', 'C*12:03', 'C*14:02', 'C*15:02', 'E*01:01'])
    __supported_length = frozenset([8, 9, 10, 11])
    __name = "netmhc"
    __command = "netMHC -p {peptides} -a {alleles} -x {out} {options}"
    __version = "3.4"

    @property
    def version(self):
        return self.__version

    def convert_alleles(self, alleles):
        return ["HLA-%s%s:%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        f = csv.reader(open(_file, "r"), delimiter='\t')
        f.next()
        f.next()
        alleles = map(lambda x: x.split()[0], f.next()[3:])
        for l in f:
            if not l:
                continue
            pep_seq = l[2]
            for ic_50, a in itertools.izip(l[3:], alleles):
                sc = 1.0 - math.log(float(ic_50), 50000)
                result[a][pep_seq] = sc if sc > 0.0 else 0.0
        return dict(result)

    def get_external_version(self, path=None):
        return super(NetMHC_3_4, self).get_external_version()

    def prepare_input(self, _input, _file):
        _file.write("\n".join(_input))


class NetMHC_3_0(NetMHC_3_4):
    """
    Implements the NetMHC binding (for netMHC3.0)


    NetMHC-3.0: accurate web accessible predictions of human, mouse and monkey MHC class I affinities for peptides of length 8-11
    Lundegaard C, Lamberth K, Harndahl M, Buus S, Lund O, Nielsen M. Nucleic Acids Res. 1;36(Web Server issue):W509-12. 2008

    Accurate approximation method for prediction of class I MHC affinities for peptides of length 8, 10 and 11 using prediction tools trained on 9mers.
    Lundegaard C, Lund O, Nielsen M. Bioinformatics, 24(11):1397-98, 2008.
    """

    __alleles = frozenset(['A*01:01', 'A*02:01', 'A*02:02', 'A*02:03', 'A*02:04', 'A*02:06', 'A*02:11', 'A*02:12',
                           'A*02:16', 'A*02:19', 'A*03:01', 'A*11:01', 'A*23:01', 'A*24:02', 'A*24:03', 'A*26:01',
                           'A*26:02', 'A*29:02', 'A*30:01', 'A*30:02', 'A*31:01', 'A*33:01', 'A*68:01', 'A*68:02',
                           'A*69:01', 'B*07:02', 'B*08:01', 'B*08:02', 'B*15:01', 'B*18:01', 'B*27:05', 'B*35:01',
                           'B*39:01', 'B*40:01', 'B*40:02', 'B*44:02', 'B*44:03', 'B*45:01', 'B*51:01', 'B*53:01',
                           'B*54:01', 'B*57:01', 'B*58:01']) #no PSSM predictors

    __supported_length = frozenset([8, 9, 10, 11])
    __name = "netmhc"
    __version = "3.0a"
    __command = "netMHC-3.0 -p {peptides} -a {alleles} -x {out} {options}"

    @property
    def version(self):
        return self.__version
    @property
    def name(self):
        return self.__name
    @property
    def command(self):
        return self.__command
    @property
    def supportedAlleles(self):
        return self.__alleles

    def convert_alleles(self, alleles):
        return ["%s%s%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    def parse_external_result(self, _file):
        result = defaultdict(dict)
        with open(_file, 'r') as f:
            next(f, None) #skip first line with logging stuff
            next(f, None) #skip first line with nothing
            csvr = csv.reader(f, delimiter='\t')
            alleles = map(lambda x: x.split()[0], csvr.next()[3:])
            for l in csvr:
                if not l:
                    continue
                pep_seq = l[2]
                for ic_50, a in itertools.izip(l[3:], alleles):
                    sc = 1.0 - math.log(float(ic_50), 50000)
                    result[a][pep_seq] = sc if sc > 0.0 else 0.0
        if 'Average' in result:
            result.pop('Average')
        return dict(result)


class NetMHCpan_2_4(AExternalEpitopePrediction):
    """
        Implements the NetMHC binding (in current form for netMHCpan 2.4)
        Supported  MHC alleles currently only restricted to HLA alleles


        Nielsen, Morten, et al. "NetMHCpan, a method for quantitative predictions of peptide binding to any HLA-A and-B locus
        protein of known sequence." PloS one 2.8 (2007): e796.
    """
    __supported_length = frozenset([8, 9, 10, 11])
    __name = "netmhcpan"
    __command = "netMHCpan -p {peptides} -a {alleles} {options} -ic50 -xls -xlsfile {out}"
    __alleles = frozenset(['A*01:01', 'A*01:02', 'A*01:03', 'A*01:06', 'A*01:07', 'A*01:08', 'A*01:09', 'A*01:10', 'A*01:12',
                 'A*01:13', 'A*01:14', 'A*01:17', 'A*01:19', 'A*01:20', 'A*01:21', 'A*01:23', 'A*01:24', 'A*01:25',
                 'A*01:26', 'A*01:28', 'A*01:29', 'A*01:30', 'A*01:32', 'A*01:33', 'A*01:35', 'A*01:36', 'A*01:37',
                 'A*01:38', 'A*01:39', 'A*01:40', 'A*01:41', 'A*01:42', 'A*01:43', 'A*01:44', 'A*01:45', 'A*01:46',
                 'A*01:47', 'A*01:48', 'A*01:49', 'A*01:50', 'A*01:51', 'A*01:54', 'A*01:55', 'A*01:58', 'A*01:59',
                 'A*01:60', 'A*01:61', 'A*01:62', 'A*01:63', 'A*01:64', 'A*01:65', 'A*01:66', 'A*02:01', 'A*02:02',
                 'A*02:03', 'A*02:04', 'A*02:05', 'A*02:06', 'A*02:07', 'A*02:08', 'A*02:09', 'A*02:10', 'A*02:101',
                 'A*02:102', 'A*02:103', 'A*02:104', 'A*02:105', 'A*02:106', 'A*02:107', 'A*02:108', 'A*02:109',
                 'A*02:11', 'A*02:110', 'A*02:111', 'A*02:112', 'A*02:114', 'A*02:115', 'A*02:116', 'A*02:117',
                 'A*02:118', 'A*02:119', 'A*02:12', 'A*02:120', 'A*02:121', 'A*02:122', 'A*02:123', 'A*02:124',
                 'A*02:126', 'A*02:127', 'A*02:128', 'A*02:129', 'A*02:13', 'A*02:130', 'A*02:131', 'A*02:132',
                 'A*02:133', 'A*02:134', 'A*02:135', 'A*02:136', 'A*02:137', 'A*02:138', 'A*02:139', 'A*02:14',
                 'A*02:140', 'A*02:141', 'A*02:142', 'A*02:143', 'A*02:144', 'A*02:145', 'A*02:146', 'A*02:147',
                 'A*02:148', 'A*02:149', 'A*02:150', 'A*02:151', 'A*02:152', 'A*02:153', 'A*02:154', 'A*02:155',
                 'A*02:156', 'A*02:157', 'A*02:158', 'A*02:159', 'A*02:16', 'A*02:160', 'A*02:161', 'A*02:162',
                 'A*02:163', 'A*02:164', 'A*02:165', 'A*02:166', 'A*02:167', 'A*02:168', 'A*02:169', 'A*02:17',
                 'A*02:170', 'A*02:171', 'A*02:172', 'A*02:173', 'A*02:174', 'A*02:175', 'A*02:176', 'A*02:177',
                 'A*02:178', 'A*02:179', 'A*02:18', 'A*02:180', 'A*02:181', 'A*02:182', 'A*02:183', 'A*02:184',
                 'A*02:185', 'A*02:186', 'A*02:187', 'A*02:188', 'A*02:189', 'A*02:19', 'A*02:190', 'A*02:191',
                 'A*02:192', 'A*02:193', 'A*02:194', 'A*02:195', 'A*02:196', 'A*02:197', 'A*02:198', 'A*02:199',
                 'A*02:20', 'A*02:200', 'A*02:201', 'A*02:202', 'A*02:203', 'A*02:204', 'A*02:205', 'A*02:206',
                 'A*02:207', 'A*02:208', 'A*02:209', 'A*02:21', 'A*02:210', 'A*02:211', 'A*02:212', 'A*02:213',
                 'A*02:214', 'A*02:215', 'A*02:216', 'A*02:217', 'A*02:218', 'A*02:219', 'A*02:22', 'A*02:220',
                 'A*02:221', 'A*02:224', 'A*02:228', 'A*02:229', 'A*02:230', 'A*02:231', 'A*02:232', 'A*02:233',
                 'A*02:234', 'A*02:235', 'A*02:236', 'A*02:237', 'A*02:238', 'A*02:239', 'A*02:24', 'A*02:240',
                 'A*02:241', 'A*02:242', 'A*02:243', 'A*02:244', 'A*02:245', 'A*02:246', 'A*02:247', 'A*02:248',
                 'A*02:249', 'A*02:25', 'A*02:251', 'A*02:252', 'A*02:253', 'A*02:254', 'A*02:255', 'A*02:256',
                 'A*02:257', 'A*02:258', 'A*02:259', 'A*02:26', 'A*02:260', 'A*02:261', 'A*02:262', 'A*02:263',
                 'A*02:264', 'A*02:265', 'A*02:266', 'A*02:27', 'A*02:28', 'A*02:29', 'A*02:30', 'A*02:31', 'A*02:33',
                 'A*02:34', 'A*02:35', 'A*02:36', 'A*02:37', 'A*02:38', 'A*02:39', 'A*02:40', 'A*02:41', 'A*02:42',
                 'A*02:44', 'A*02:45', 'A*02:46', 'A*02:47', 'A*02:48', 'A*02:49', 'A*02:50', 'A*02:51', 'A*02:52',
                 'A*02:54', 'A*02:55', 'A*02:56', 'A*02:57', 'A*02:58', 'A*02:59', 'A*02:60', 'A*02:61', 'A*02:62',
                 'A*02:63', 'A*02:64', 'A*02:65', 'A*02:66', 'A*02:67', 'A*02:68', 'A*02:69', 'A*02:70', 'A*02:71',
                 'A*02:72', 'A*02:73', 'A*02:74', 'A*02:75', 'A*02:76', 'A*02:77', 'A*02:78', 'A*02:79', 'A*02:80',
                 'A*02:81', 'A*02:84', 'A*02:85', 'A*02:86', 'A*02:87', 'A*02:89', 'A*02:90', 'A*02:91', 'A*02:92',
                 'A*02:93', 'A*02:95', 'A*02:96', 'A*02:97', 'A*02:99', 'A*03:01', 'A*03:02', 'A*03:04', 'A*03:05',
                 'A*03:06', 'A*03:07', 'A*03:08', 'A*03:09', 'A*03:10', 'A*03:12', 'A*03:13', 'A*03:14', 'A*03:15',
                 'A*03:16', 'A*03:17', 'A*03:18', 'A*03:19', 'A*03:20', 'A*03:22', 'A*03:23', 'A*03:24', 'A*03:25',
                 'A*03:26', 'A*03:27', 'A*03:28', 'A*03:29', 'A*03:30', 'A*03:31', 'A*03:32', 'A*03:33', 'A*03:34',
                 'A*03:35', 'A*03:37', 'A*03:38', 'A*03:39', 'A*03:40', 'A*03:41', 'A*03:42', 'A*03:43', 'A*03:44',
                 'A*03:45', 'A*03:46', 'A*03:47', 'A*03:48', 'A*03:49', 'A*03:50', 'A*03:51', 'A*03:52', 'A*03:53',
                 'A*03:54', 'A*03:55', 'A*03:56', 'A*03:57', 'A*03:58', 'A*03:59', 'A*03:60', 'A*03:61', 'A*03:62',
                 'A*03:63', 'A*03:64', 'A*03:65', 'A*03:66', 'A*03:67', 'A*03:70', 'A*03:71', 'A*03:72', 'A*03:73',
                 'A*03:74', 'A*03:75', 'A*03:76', 'A*03:77', 'A*03:78', 'A*03:79', 'A*03:80', 'A*03:81', 'A*03:82',
                 'A*11:01', 'A*11:02', 'A*11:03', 'A*11:04', 'A*11:05', 'A*11:06', 'A*11:07', 'A*11:08', 'A*11:09',
                 'A*11:10', 'A*11:11', 'A*11:12', 'A*11:13', 'A*11:14', 'A*11:15', 'A*11:16', 'A*11:17', 'A*11:18',
                 'A*11:19', 'A*11:20', 'A*11:22', 'A*11:23', 'A*11:24', 'A*11:25', 'A*11:26', 'A*11:27', 'A*11:29',
                 'A*11:30', 'A*11:31', 'A*11:32', 'A*11:33', 'A*11:34', 'A*11:35', 'A*11:36', 'A*11:37', 'A*11:38',
                 'A*11:39', 'A*11:40', 'A*11:41', 'A*11:42', 'A*11:43', 'A*11:44', 'A*11:45', 'A*11:46', 'A*11:47',
                 'A*11:48', 'A*11:49', 'A*11:51', 'A*11:53', 'A*11:54', 'A*11:55', 'A*11:56', 'A*11:57', 'A*11:58',
                 'A*11:59', 'A*11:60', 'A*11:61', 'A*11:62', 'A*11:63', 'A*11:64', 'A*23:01', 'A*23:02', 'A*23:03',
                 'A*23:04', 'A*23:05', 'A*23:06', 'A*23:09', 'A*23:10', 'A*23:12', 'A*23:13', 'A*23:14', 'A*23:15',
                 'A*23:16', 'A*23:17', 'A*23:18', 'A*23:20', 'A*23:21', 'A*23:22', 'A*23:23', 'A*23:24', 'A*23:25',
                 'A*23:26', 'A*24:02', 'A*24:03', 'A*24:04', 'A*24:05', 'A*24:06', 'A*24:07', 'A*24:08', 'A*24:10',
                 'A*24:100', 'A*24:101', 'A*24:102', 'A*24:103', 'A*24:104', 'A*24:105', 'A*24:106', 'A*24:107',
                 'A*24:108', 'A*24:109', 'A*24:110', 'A*24:111', 'A*24:112', 'A*24:113', 'A*24:114', 'A*24:115',
                 'A*24:116', 'A*24:117', 'A*24:118', 'A*24:119', 'A*24:120', 'A*24:121', 'A*24:122', 'A*24:123',
                 'A*24:124', 'A*24:125', 'A*24:126', 'A*24:127', 'A*24:128', 'A*24:129', 'A*24:13', 'A*24:130',
                 'A*24:131', 'A*24:133', 'A*24:134', 'A*24:135', 'A*24:136', 'A*24:137', 'A*24:138', 'A*24:139',
                 'A*24:14', 'A*24:140', 'A*24:141', 'A*24:142', 'A*24:143', 'A*24:144', 'A*24:15', 'A*24:17', 'A*24:18',
                 'A*24:19', 'A*24:20', 'A*24:21', 'A*24:22', 'A*24:23', 'A*24:24', 'A*24:25', 'A*24:26', 'A*24:27',
                 'A*24:28', 'A*24:29', 'A*24:30', 'A*24:31', 'A*24:32', 'A*24:33', 'A*24:34', 'A*24:35', 'A*24:37',
                 'A*24:38', 'A*24:39', 'A*24:41', 'A*24:42', 'A*24:43', 'A*24:44', 'A*24:46', 'A*24:47', 'A*24:49',
                 'A*24:50', 'A*24:51', 'A*24:52', 'A*24:53', 'A*24:54', 'A*24:55', 'A*24:56', 'A*24:57', 'A*24:58',
                 'A*24:59', 'A*24:61', 'A*24:62', 'A*24:63', 'A*24:64', 'A*24:66', 'A*24:67', 'A*24:68', 'A*24:69',
                 'A*24:70', 'A*24:71', 'A*24:72', 'A*24:73', 'A*24:74', 'A*24:75', 'A*24:76', 'A*24:77', 'A*24:78',
                 'A*24:79', 'A*24:80', 'A*24:81', 'A*24:82', 'A*24:85', 'A*24:87', 'A*24:88', 'A*24:89', 'A*24:91',
                 'A*24:92', 'A*24:93', 'A*24:94', 'A*24:95', 'A*24:96', 'A*24:97', 'A*24:98', 'A*24:99', 'A*25:01',
                 'A*25:02', 'A*25:03', 'A*25:04', 'A*25:05', 'A*25:06', 'A*25:07', 'A*25:08', 'A*25:09', 'A*25:10',
                 'A*25:11', 'A*25:13', 'A*26:01', 'A*26:02', 'A*26:03', 'A*26:04', 'A*26:05', 'A*26:06', 'A*26:07',
                 'A*26:08', 'A*26:09', 'A*26:10', 'A*26:12', 'A*26:13', 'A*26:14', 'A*26:15', 'A*26:16', 'A*26:17',
                 'A*26:18', 'A*26:19', 'A*26:20', 'A*26:21', 'A*26:22', 'A*26:23', 'A*26:24', 'A*26:26', 'A*26:27',
                 'A*26:28', 'A*26:29', 'A*26:30', 'A*26:31', 'A*26:32', 'A*26:33', 'A*26:34', 'A*26:35', 'A*26:36',
                 'A*26:37', 'A*26:38', 'A*26:39', 'A*26:40', 'A*26:41', 'A*26:42', 'A*26:43', 'A*26:45', 'A*26:46',
                 'A*26:47', 'A*26:48', 'A*26:49', 'A*26:50', 'A*29:01', 'A*29:02', 'A*29:03', 'A*29:04', 'A*29:05',
                 'A*29:06', 'A*29:07', 'A*29:09', 'A*29:10', 'A*29:11', 'A*29:12', 'A*29:13', 'A*29:14', 'A*29:15',
                 'A*29:16', 'A*29:17', 'A*29:18', 'A*29:19', 'A*29:20', 'A*29:21', 'A*29:22', 'A*30:01', 'A*30:02',
                 'A*30:03', 'A*30:04', 'A*30:06', 'A*30:07', 'A*30:08', 'A*30:09', 'A*30:10', 'A*30:11', 'A*30:12',
                 'A*30:13', 'A*30:15', 'A*30:16', 'A*30:17', 'A*30:18', 'A*30:19', 'A*30:20', 'A*30:22', 'A*30:23',
                 'A*30:24', 'A*30:25', 'A*30:26', 'A*30:28', 'A*30:29', 'A*30:30', 'A*30:31', 'A*30:32', 'A*30:33',
                 'A*30:34', 'A*30:35', 'A*30:36', 'A*30:37', 'A*30:38', 'A*30:39', 'A*30:40', 'A*30:41', 'A*31:01',
                 'A*31:02', 'A*31:03', 'A*31:04', 'A*31:05', 'A*31:06', 'A*31:07', 'A*31:08', 'A*31:09', 'A*31:10',
                 'A*31:11', 'A*31:12', 'A*31:13', 'A*31:15', 'A*31:16', 'A*31:17', 'A*31:18', 'A*31:19', 'A*31:20',
                 'A*31:21', 'A*31:22', 'A*31:23', 'A*31:24', 'A*31:25', 'A*31:26', 'A*31:27', 'A*31:28', 'A*31:29',
                 'A*31:30', 'A*31:31', 'A*31:32', 'A*31:33', 'A*31:34', 'A*31:35', 'A*31:36', 'A*31:37', 'A*32:01',
                 'A*32:02', 'A*32:03', 'A*32:04', 'A*32:05', 'A*32:06', 'A*32:07', 'A*32:08', 'A*32:09', 'A*32:10',
                 'A*32:12', 'A*32:13', 'A*32:14', 'A*32:15', 'A*32:16', 'A*32:17', 'A*32:18', 'A*32:20', 'A*32:21',
                 'A*32:22', 'A*32:23', 'A*32:24', 'A*32:25', 'A*33:01', 'A*33:03', 'A*33:04', 'A*33:05', 'A*33:06',
                 'A*33:07', 'A*33:08', 'A*33:09', 'A*33:10', 'A*33:11', 'A*33:12', 'A*33:13', 'A*33:14', 'A*33:15',
                 'A*33:16', 'A*33:17', 'A*33:18', 'A*33:19', 'A*33:20', 'A*33:21', 'A*33:22', 'A*33:23', 'A*33:24',
                 'A*33:25', 'A*33:26', 'A*33:27', 'A*33:28', 'A*33:29', 'A*33:30', 'A*33:31', 'A*34:01', 'A*34:02',
                 'A*34:03', 'A*34:04', 'A*34:05', 'A*34:06', 'A*34:07', 'A*34:08', 'A*36:01', 'A*36:02', 'A*36:03',
                 'A*36:04', 'A*36:05', 'A*43:01', 'A*66:01', 'A*66:02', 'A*66:03', 'A*66:04', 'A*66:05', 'A*66:06',
                 'A*66:07', 'A*66:08', 'A*66:09', 'A*66:10', 'A*66:11', 'A*66:12', 'A*66:13', 'A*66:14', 'A*66:15',
                 'A*68:01', 'A*68:02', 'A*68:03', 'A*68:04', 'A*68:05', 'A*68:06', 'A*68:07', 'A*68:08', 'A*68:09',
                 'A*68:10', 'A*68:12', 'A*68:13', 'A*68:14', 'A*68:15', 'A*68:16', 'A*68:17', 'A*68:19', 'A*68:20',
                 'A*68:21', 'A*68:22', 'A*68:23', 'A*68:24', 'A*68:25', 'A*68:26', 'A*68:27', 'A*68:28', 'A*68:29',
                 'A*68:30', 'A*68:31', 'A*68:32', 'A*68:33', 'A*68:34', 'A*68:35', 'A*68:36', 'A*68:37', 'A*68:38',
                 'A*68:39', 'A*68:40', 'A*68:41', 'A*68:42', 'A*68:43', 'A*68:44', 'A*68:45', 'A*68:46', 'A*68:47',
                 'A*68:48', 'A*68:50', 'A*68:51', 'A*68:52', 'A*68:53', 'A*68:54', 'A*69:01', 'A*74:01', 'A*74:02',
                 'A*74:03', 'A*74:04', 'A*74:05', 'A*74:06', 'A*74:07', 'A*74:08', 'A*74:09', 'A*74:10', 'A*74:11',
                 'A*74:13', 'A*80:01', 'A*80:02', 'B*07:02', 'B*07:03', 'B*07:04', 'B*07:05', 'B*07:06', 'B*07:07',
                 'B*07:08', 'B*07:09', 'B*07:10', 'B*07:100', 'B*07:101', 'B*07:102', 'B*07:103', 'B*07:104',
                 'B*07:105', 'B*07:106', 'B*07:107', 'B*07:108', 'B*07:109', 'B*07:11', 'B*07:110', 'B*07:112',
                 'B*07:113', 'B*07:114', 'B*07:115', 'B*07:12', 'B*07:13', 'B*07:14', 'B*07:15', 'B*07:16', 'B*07:17',
                 'B*07:18', 'B*07:19', 'B*07:20', 'B*07:21', 'B*07:22', 'B*07:23', 'B*07:24', 'B*07:25', 'B*07:26',
                 'B*07:27', 'B*07:28', 'B*07:29', 'B*07:30', 'B*07:31', 'B*07:32', 'B*07:33', 'B*07:34', 'B*07:35',
                 'B*07:36', 'B*07:37', 'B*07:38', 'B*07:39', 'B*07:40', 'B*07:41', 'B*07:42', 'B*07:43', 'B*07:44',
                 'B*07:45', 'B*07:46', 'B*07:47', 'B*07:48', 'B*07:50', 'B*07:51', 'B*07:52', 'B*07:53', 'B*07:54',
                 'B*07:55', 'B*07:56', 'B*07:57', 'B*07:58', 'B*07:59', 'B*07:60', 'B*07:61', 'B*07:62', 'B*07:63',
                 'B*07:64', 'B*07:65', 'B*07:66', 'B*07:68', 'B*07:69', 'B*07:70', 'B*07:71', 'B*07:72', 'B*07:73',
                 'B*07:74', 'B*07:75', 'B*07:76', 'B*07:77', 'B*07:78', 'B*07:79', 'B*07:80', 'B*07:81', 'B*07:82',
                 'B*07:83', 'B*07:84', 'B*07:85', 'B*07:86', 'B*07:87', 'B*07:88', 'B*07:89', 'B*07:90', 'B*07:91',
                 'B*07:92', 'B*07:93', 'B*07:94', 'B*07:95', 'B*07:96', 'B*07:97', 'B*07:98', 'B*07:99', 'B*08:01',
                 'B*08:02', 'B*08:03', 'B*08:04', 'B*08:05', 'B*08:07', 'B*08:09', 'B*08:10', 'B*08:11', 'B*08:12',
                 'B*08:13', 'B*08:14', 'B*08:15', 'B*08:16', 'B*08:17', 'B*08:18', 'B*08:20', 'B*08:21', 'B*08:22',
                 'B*08:23', 'B*08:24', 'B*08:25', 'B*08:26', 'B*08:27', 'B*08:28', 'B*08:29', 'B*08:31', 'B*08:32',
                 'B*08:33', 'B*08:34', 'B*08:35', 'B*08:36', 'B*08:37', 'B*08:38', 'B*08:39', 'B*08:40', 'B*08:41',
                 'B*08:42', 'B*08:43', 'B*08:44', 'B*08:45', 'B*08:46', 'B*08:47', 'B*08:48', 'B*08:49', 'B*08:50',
                 'B*08:51', 'B*08:52', 'B*08:53', 'B*08:54', 'B*08:55', 'B*08:56', 'B*08:57', 'B*08:58', 'B*08:59',
                 'B*08:60', 'B*08:61', 'B*08:62', 'B*13:01', 'B*13:02', 'B*13:03', 'B*13:04', 'B*13:06', 'B*13:09',
                 'B*13:10', 'B*13:11', 'B*13:12', 'B*13:13', 'B*13:14', 'B*13:15', 'B*13:16', 'B*13:17', 'B*13:18',
                 'B*13:19', 'B*13:20', 'B*13:21', 'B*13:22', 'B*13:23', 'B*13:25', 'B*13:26', 'B*13:27', 'B*13:28',
                 'B*13:29', 'B*13:30', 'B*13:31', 'B*13:32', 'B*13:33', 'B*13:34', 'B*13:35', 'B*13:36', 'B*13:37',
                 'B*13:38', 'B*13:39', 'B*14:01', 'B*14:02', 'B*14:03', 'B*14:04', 'B*14:05', 'B*14:06', 'B*14:08',
                 'B*14:09', 'B*14:10', 'B*14:11', 'B*14:12', 'B*14:13', 'B*14:14', 'B*14:15', 'B*14:16', 'B*14:17',
                 'B*14:18', 'B*15:01', 'B*15:02', 'B*15:03', 'B*15:04', 'B*15:05', 'B*15:06', 'B*15:07', 'B*15:08',
                 'B*15:09', 'B*15:10', 'B*15:101', 'B*15:102', 'B*15:103', 'B*15:104', 'B*15:105', 'B*15:106',
                 'B*15:107', 'B*15:108', 'B*15:109', 'B*15:11', 'B*15:110', 'B*15:112', 'B*15:113', 'B*15:114',
                 'B*15:115', 'B*15:116', 'B*15:117', 'B*15:118', 'B*15:119', 'B*15:12', 'B*15:120', 'B*15:121',
                 'B*15:122', 'B*15:123', 'B*15:124', 'B*15:125', 'B*15:126', 'B*15:127', 'B*15:128', 'B*15:129',
                 'B*15:13', 'B*15:131', 'B*15:132', 'B*15:133', 'B*15:134', 'B*15:135', 'B*15:136', 'B*15:137',
                 'B*15:138', 'B*15:139', 'B*15:14', 'B*15:140', 'B*15:141', 'B*15:142', 'B*15:143', 'B*15:144',
                 'B*15:145', 'B*15:146', 'B*15:147', 'B*15:148', 'B*15:15', 'B*15:150', 'B*15:151', 'B*15:152',
                 'B*15:153', 'B*15:154', 'B*15:155', 'B*15:156', 'B*15:157', 'B*15:158', 'B*15:159', 'B*15:16',
                 'B*15:160', 'B*15:161', 'B*15:162', 'B*15:163', 'B*15:164', 'B*15:165', 'B*15:166', 'B*15:167',
                 'B*15:168', 'B*15:169', 'B*15:17', 'B*15:170', 'B*15:171', 'B*15:172', 'B*15:173', 'B*15:174',
                 'B*15:175', 'B*15:176', 'B*15:177', 'B*15:178', 'B*15:179', 'B*15:18', 'B*15:180', 'B*15:183',
                 'B*15:184', 'B*15:185', 'B*15:186', 'B*15:187', 'B*15:188', 'B*15:189', 'B*15:19', 'B*15:191',
                 'B*15:192', 'B*15:193', 'B*15:194', 'B*15:195', 'B*15:196', 'B*15:197', 'B*15:198', 'B*15:199',
                 'B*15:20', 'B*15:200', 'B*15:201', 'B*15:202', 'B*15:21', 'B*15:23', 'B*15:24', 'B*15:25', 'B*15:27',
                 'B*15:28', 'B*15:29', 'B*15:30', 'B*15:31', 'B*15:32', 'B*15:33', 'B*15:34', 'B*15:35', 'B*15:36',
                 'B*15:37', 'B*15:38', 'B*15:39', 'B*15:40', 'B*15:42', 'B*15:43', 'B*15:44', 'B*15:45', 'B*15:46',
                 'B*15:47', 'B*15:48', 'B*15:49', 'B*15:50', 'B*15:51', 'B*15:52', 'B*15:53', 'B*15:54', 'B*15:55',
                 'B*15:56', 'B*15:57', 'B*15:58', 'B*15:60', 'B*15:61', 'B*15:62', 'B*15:63', 'B*15:64', 'B*15:65',
                 'B*15:66', 'B*15:67', 'B*15:68', 'B*15:69', 'B*15:70', 'B*15:71', 'B*15:72', 'B*15:73', 'B*15:74',
                 'B*15:75', 'B*15:76', 'B*15:77', 'B*15:78', 'B*15:80', 'B*15:81', 'B*15:82', 'B*15:83', 'B*15:84',
                 'B*15:85', 'B*15:86', 'B*15:87', 'B*15:88', 'B*15:89', 'B*15:90', 'B*15:91', 'B*15:92', 'B*15:93',
                 'B*15:95', 'B*15:96', 'B*15:97', 'B*15:98', 'B*15:99', 'B*18:01', 'B*18:02', 'B*18:03', 'B*18:04',
                 'B*18:05', 'B*18:06', 'B*18:07', 'B*18:08', 'B*18:09', 'B*18:10', 'B*18:11', 'B*18:12', 'B*18:13',
                 'B*18:14', 'B*18:15', 'B*18:18', 'B*18:19', 'B*18:20', 'B*18:21', 'B*18:22', 'B*18:24', 'B*18:25',
                 'B*18:26', 'B*18:27', 'B*18:28', 'B*18:29', 'B*18:30', 'B*18:31', 'B*18:32', 'B*18:33', 'B*18:34',
                 'B*18:35', 'B*18:36', 'B*18:37', 'B*18:38', 'B*18:39', 'B*18:40', 'B*18:41', 'B*18:42', 'B*18:43',
                 'B*18:44', 'B*18:45', 'B*18:46', 'B*18:47', 'B*18:48', 'B*18:49', 'B*18:50', 'B*27:01', 'B*27:02',
                 'B*27:03', 'B*27:04', 'B*27:05', 'B*27:06', 'B*27:07', 'B*27:08', 'B*27:09', 'B*27:10', 'B*27:11',
                 'B*27:12', 'B*27:13', 'B*27:14', 'B*27:15', 'B*27:16', 'B*27:17', 'B*27:18', 'B*27:19', 'B*27:20',
                 'B*27:21', 'B*27:23', 'B*27:24', 'B*27:25', 'B*27:26', 'B*27:27', 'B*27:28', 'B*27:29', 'B*27:30',
                 'B*27:31', 'B*27:32', 'B*27:33', 'B*27:34', 'B*27:35', 'B*27:36', 'B*27:37', 'B*27:38', 'B*27:39',
                 'B*27:40', 'B*27:41', 'B*27:42', 'B*27:43', 'B*27:44', 'B*27:45', 'B*27:46', 'B*27:47', 'B*27:48',
                 'B*27:49', 'B*27:50', 'B*27:51', 'B*27:52', 'B*27:53', 'B*27:54', 'B*27:55', 'B*27:56', 'B*27:57',
                 'B*27:58', 'B*27:60', 'B*27:61', 'B*27:62', 'B*27:63', 'B*27:67', 'B*27:68', 'B*27:69', 'B*35:01',
                 'B*35:02', 'B*35:03', 'B*35:04', 'B*35:05', 'B*35:06', 'B*35:07', 'B*35:08', 'B*35:09', 'B*35:10',
                 'B*35:100', 'B*35:101', 'B*35:102', 'B*35:103', 'B*35:104', 'B*35:105', 'B*35:106', 'B*35:107',
                 'B*35:108', 'B*35:109', 'B*35:11', 'B*35:110', 'B*35:111', 'B*35:112', 'B*35:113', 'B*35:114',
                 'B*35:115', 'B*35:116', 'B*35:117', 'B*35:118', 'B*35:119', 'B*35:12', 'B*35:120', 'B*35:121',
                 'B*35:122', 'B*35:123', 'B*35:124', 'B*35:125', 'B*35:126', 'B*35:127', 'B*35:128', 'B*35:13',
                 'B*35:131', 'B*35:132', 'B*35:133', 'B*35:135', 'B*35:136', 'B*35:137', 'B*35:138', 'B*35:139',
                 'B*35:14', 'B*35:140', 'B*35:141', 'B*35:142', 'B*35:143', 'B*35:144', 'B*35:15', 'B*35:16', 'B*35:17',
                 'B*35:18', 'B*35:19', 'B*35:20', 'B*35:21', 'B*35:22', 'B*35:23', 'B*35:24', 'B*35:25', 'B*35:26',
                 'B*35:27', 'B*35:28', 'B*35:29', 'B*35:30', 'B*35:31', 'B*35:32', 'B*35:33', 'B*35:34', 'B*35:35',
                 'B*35:36', 'B*35:37', 'B*35:38', 'B*35:39', 'B*35:41', 'B*35:42', 'B*35:43', 'B*35:44', 'B*35:45',
                 'B*35:46', 'B*35:47', 'B*35:48', 'B*35:49', 'B*35:50', 'B*35:51', 'B*35:52', 'B*35:54', 'B*35:55',
                 'B*35:56', 'B*35:57', 'B*35:58', 'B*35:59', 'B*35:60', 'B*35:61', 'B*35:62', 'B*35:63', 'B*35:64',
                 'B*35:66', 'B*35:67', 'B*35:68', 'B*35:69', 'B*35:70', 'B*35:71', 'B*35:72', 'B*35:74', 'B*35:75',
                 'B*35:76', 'B*35:77', 'B*35:78', 'B*35:79', 'B*35:80', 'B*35:81', 'B*35:82', 'B*35:83', 'B*35:84',
                 'B*35:85', 'B*35:86', 'B*35:87', 'B*35:88', 'B*35:89', 'B*35:90', 'B*35:91', 'B*35:92', 'B*35:93',
                 'B*35:94', 'B*35:95', 'B*35:96', 'B*35:97', 'B*35:98', 'B*35:99', 'B*37:01', 'B*37:02', 'B*37:04',
                 'B*37:05', 'B*37:06', 'B*37:07', 'B*37:08', 'B*37:09', 'B*37:10', 'B*37:11', 'B*37:12', 'B*37:13',
                 'B*37:14', 'B*37:15', 'B*37:17', 'B*37:18', 'B*37:19', 'B*37:20', 'B*37:21', 'B*37:22', 'B*37:23',
                 'B*38:01', 'B*38:02', 'B*38:03', 'B*38:04', 'B*38:05', 'B*38:06', 'B*38:07', 'B*38:08', 'B*38:09',
                 'B*38:10', 'B*38:11', 'B*38:12', 'B*38:13', 'B*38:14', 'B*38:15', 'B*38:16', 'B*38:17', 'B*38:18',
                 'B*38:19', 'B*38:20', 'B*38:21', 'B*38:22', 'B*38:23', 'B*39:01', 'B*39:02', 'B*39:03', 'B*39:04',
                 'B*39:05', 'B*39:06', 'B*39:07', 'B*39:08', 'B*39:09', 'B*39:10', 'B*39:11', 'B*39:12', 'B*39:13',
                 'B*39:14', 'B*39:15', 'B*39:16', 'B*39:17', 'B*39:18', 'B*39:19', 'B*39:20', 'B*39:22', 'B*39:23',
                 'B*39:24', 'B*39:26', 'B*39:27', 'B*39:28', 'B*39:29', 'B*39:30', 'B*39:31', 'B*39:32', 'B*39:33',
                 'B*39:34', 'B*39:35', 'B*39:36', 'B*39:37', 'B*39:39', 'B*39:41', 'B*39:42', 'B*39:43', 'B*39:44',
                 'B*39:45', 'B*39:46', 'B*39:47', 'B*39:48', 'B*39:49', 'B*39:50', 'B*39:51', 'B*39:52', 'B*39:53',
                 'B*39:54', 'B*39:55', 'B*39:56', 'B*39:57', 'B*39:58', 'B*39:59', 'B*39:60', 'B*40:01', 'B*40:02',
                 'B*40:03', 'B*40:04', 'B*40:05', 'B*40:06', 'B*40:07', 'B*40:08', 'B*40:09', 'B*40:10', 'B*40:100',
                 'B*40:101', 'B*40:102', 'B*40:103', 'B*40:104', 'B*40:105', 'B*40:106', 'B*40:107', 'B*40:108',
                 'B*40:109', 'B*40:11', 'B*40:110', 'B*40:111', 'B*40:112', 'B*40:113', 'B*40:114', 'B*40:115',
                 'B*40:116', 'B*40:117', 'B*40:119', 'B*40:12', 'B*40:120', 'B*40:121', 'B*40:122', 'B*40:123',
                 'B*40:124', 'B*40:125', 'B*40:126', 'B*40:127', 'B*40:128', 'B*40:129', 'B*40:13', 'B*40:130',
                 'B*40:131', 'B*40:132', 'B*40:134', 'B*40:135', 'B*40:136', 'B*40:137', 'B*40:138', 'B*40:139',
                 'B*40:14', 'B*40:140', 'B*40:141', 'B*40:143', 'B*40:145', 'B*40:146', 'B*40:147', 'B*40:15',
                 'B*40:16', 'B*40:18', 'B*40:19', 'B*40:20', 'B*40:21', 'B*40:23', 'B*40:24', 'B*40:25', 'B*40:26',
                 'B*40:27', 'B*40:28', 'B*40:29', 'B*40:30', 'B*40:31', 'B*40:32', 'B*40:33', 'B*40:34', 'B*40:35',
                 'B*40:36', 'B*40:37', 'B*40:38', 'B*40:39', 'B*40:40', 'B*40:42', 'B*40:43', 'B*40:44', 'B*40:45',
                 'B*40:46', 'B*40:47', 'B*40:48', 'B*40:49', 'B*40:50', 'B*40:51', 'B*40:52', 'B*40:53', 'B*40:54',
                 'B*40:55', 'B*40:56', 'B*40:57', 'B*40:58', 'B*40:59', 'B*40:60', 'B*40:61', 'B*40:62', 'B*40:63',
                 'B*40:64', 'B*40:65', 'B*40:66', 'B*40:67', 'B*40:68', 'B*40:69', 'B*40:70', 'B*40:71', 'B*40:72',
                 'B*40:73', 'B*40:74', 'B*40:75', 'B*40:76', 'B*40:77', 'B*40:78', 'B*40:79', 'B*40:80', 'B*40:81',
                 'B*40:82', 'B*40:83', 'B*40:84', 'B*40:85', 'B*40:86', 'B*40:87', 'B*40:88', 'B*40:89', 'B*40:90',
                 'B*40:91', 'B*40:92', 'B*40:93', 'B*40:94', 'B*40:95', 'B*40:96', 'B*40:97', 'B*40:98', 'B*40:99',
                 'B*41:01', 'B*41:02', 'B*41:03', 'B*41:04', 'B*41:05', 'B*41:06', 'B*41:07', 'B*41:08', 'B*41:09',
                 'B*41:10', 'B*41:11', 'B*41:12', 'B*42:01', 'B*42:02', 'B*42:04', 'B*42:05', 'B*42:06', 'B*42:07',
                 'B*42:08', 'B*42:09', 'B*42:10', 'B*42:11', 'B*42:12', 'B*42:13', 'B*42:14', 'B*44:02', 'B*44:03',
                 'B*44:04', 'B*44:05', 'B*44:06', 'B*44:07', 'B*44:08', 'B*44:09', 'B*44:10', 'B*44:100', 'B*44:101',
                 'B*44:102', 'B*44:103', 'B*44:104', 'B*44:105', 'B*44:106', 'B*44:107', 'B*44:109', 'B*44:11',
                 'B*44:110', 'B*44:12', 'B*44:13', 'B*44:14', 'B*44:15', 'B*44:16', 'B*44:17', 'B*44:18', 'B*44:20',
                 'B*44:21', 'B*44:22', 'B*44:24', 'B*44:25', 'B*44:26', 'B*44:27', 'B*44:28', 'B*44:29', 'B*44:30',
                 'B*44:31', 'B*44:32', 'B*44:33', 'B*44:34', 'B*44:35', 'B*44:36', 'B*44:37', 'B*44:38', 'B*44:39',
                 'B*44:40', 'B*44:41', 'B*44:42', 'B*44:43', 'B*44:44', 'B*44:45', 'B*44:46', 'B*44:47', 'B*44:48',
                 'B*44:49', 'B*44:50', 'B*44:51', 'B*44:53', 'B*44:54', 'B*44:55', 'B*44:57', 'B*44:59', 'B*44:60',
                 'B*44:62', 'B*44:63', 'B*44:64', 'B*44:65', 'B*44:66', 'B*44:67', 'B*44:68', 'B*44:69', 'B*44:70',
                 'B*44:71', 'B*44:72', 'B*44:73', 'B*44:74', 'B*44:75', 'B*44:76', 'B*44:77', 'B*44:78', 'B*44:79',
                 'B*44:80', 'B*44:81', 'B*44:82', 'B*44:83', 'B*44:84', 'B*44:85', 'B*44:86', 'B*44:87', 'B*44:88',
                 'B*44:89', 'B*44:90', 'B*44:91', 'B*44:92', 'B*44:93', 'B*44:94', 'B*44:95', 'B*44:96', 'B*44:97',
                 'B*44:98', 'B*44:99', 'B*45:01', 'B*45:02', 'B*45:03', 'B*45:04', 'B*45:05', 'B*45:06', 'B*45:07',
                 'B*45:08', 'B*45:09', 'B*45:10', 'B*45:11', 'B*45:12', 'B*46:01', 'B*46:02', 'B*46:03', 'B*46:04',
                 'B*46:05', 'B*46:06', 'B*46:08', 'B*46:09', 'B*46:10', 'B*46:11', 'B*46:12', 'B*46:13', 'B*46:14',
                 'B*46:16', 'B*46:17', 'B*46:18', 'B*46:19', 'B*46:20', 'B*46:21', 'B*46:22', 'B*46:23', 'B*46:24',
                 'B*47:01', 'B*47:02', 'B*47:03', 'B*47:04', 'B*47:05', 'B*47:06', 'B*47:07', 'B*48:01', 'B*48:02',
                 'B*48:03', 'B*48:04', 'B*48:05', 'B*48:06', 'B*48:07', 'B*48:08', 'B*48:09', 'B*48:10', 'B*48:11',
                 'B*48:12', 'B*48:13', 'B*48:14', 'B*48:15', 'B*48:16', 'B*48:17', 'B*48:18', 'B*48:19', 'B*48:20',
                 'B*48:21', 'B*48:22', 'B*48:23', 'B*49:01', 'B*49:02', 'B*49:03', 'B*49:04', 'B*49:05', 'B*49:06',
                 'B*49:07', 'B*49:08', 'B*49:09', 'B*49:10', 'B*50:01', 'B*50:02', 'B*50:04', 'B*50:05', 'B*50:06',
                 'B*50:07', 'B*50:08', 'B*50:09', 'B*51:01', 'B*51:02', 'B*51:03', 'B*51:04', 'B*51:05', 'B*51:06',
                 'B*51:07', 'B*51:08', 'B*51:09', 'B*51:12', 'B*51:13', 'B*51:14', 'B*51:15', 'B*51:16', 'B*51:17',
                 'B*51:18', 'B*51:19', 'B*51:20', 'B*51:21', 'B*51:22', 'B*51:23', 'B*51:24', 'B*51:26', 'B*51:28',
                 'B*51:29', 'B*51:30', 'B*51:31', 'B*51:32', 'B*51:33', 'B*51:34', 'B*51:35', 'B*51:36', 'B*51:37',
                 'B*51:38', 'B*51:39', 'B*51:40', 'B*51:42', 'B*51:43', 'B*51:45', 'B*51:46', 'B*51:48', 'B*51:49',
                 'B*51:50', 'B*51:51', 'B*51:52', 'B*51:53', 'B*51:54', 'B*51:55', 'B*51:56', 'B*51:57', 'B*51:58',
                 'B*51:59', 'B*51:60', 'B*51:61', 'B*51:62', 'B*51:63', 'B*51:64', 'B*51:65', 'B*51:66', 'B*51:67',
                 'B*51:68', 'B*51:69', 'B*51:70', 'B*51:71', 'B*51:72', 'B*51:73', 'B*51:74', 'B*51:75', 'B*51:76',
                 'B*51:77', 'B*51:78', 'B*51:79', 'B*51:80', 'B*51:81', 'B*51:82', 'B*51:83', 'B*51:84', 'B*51:85',
                 'B*51:86', 'B*51:87', 'B*51:88', 'B*51:89', 'B*51:90', 'B*51:91', 'B*51:92', 'B*51:93', 'B*51:94',
                 'B*51:95', 'B*51:96', 'B*52:01', 'B*52:02', 'B*52:03', 'B*52:04', 'B*52:05', 'B*52:06', 'B*52:07',
                 'B*52:08', 'B*52:09', 'B*52:10', 'B*52:11', 'B*52:12', 'B*52:13', 'B*52:14', 'B*52:15', 'B*52:16',
                 'B*52:17', 'B*52:18', 'B*52:19', 'B*52:20', 'B*52:21', 'B*53:01', 'B*53:02', 'B*53:03', 'B*53:04',
                 'B*53:05', 'B*53:06', 'B*53:07', 'B*53:08', 'B*53:09', 'B*53:10', 'B*53:11', 'B*53:12', 'B*53:13',
                 'B*53:14', 'B*53:15', 'B*53:16', 'B*53:17', 'B*53:18', 'B*53:19', 'B*53:20', 'B*53:21', 'B*53:22',
                 'B*53:23', 'B*54:01', 'B*54:02', 'B*54:03', 'B*54:04', 'B*54:06', 'B*54:07', 'B*54:09', 'B*54:10',
                 'B*54:11', 'B*54:12', 'B*54:13', 'B*54:14', 'B*54:15', 'B*54:16', 'B*54:17', 'B*54:18', 'B*54:19',
                 'B*54:20', 'B*54:21', 'B*54:22', 'B*54:23', 'B*55:01', 'B*55:02', 'B*55:03', 'B*55:04', 'B*55:05',
                 'B*55:07', 'B*55:08', 'B*55:09', 'B*55:10', 'B*55:11', 'B*55:12', 'B*55:13', 'B*55:14', 'B*55:15',
                 'B*55:16', 'B*55:17', 'B*55:18', 'B*55:19', 'B*55:20', 'B*55:21', 'B*55:22', 'B*55:23', 'B*55:24',
                 'B*55:25', 'B*55:26', 'B*55:27', 'B*55:28', 'B*55:29', 'B*55:30', 'B*55:31', 'B*55:32', 'B*55:33',
                 'B*55:34', 'B*55:35', 'B*55:36', 'B*55:37', 'B*55:38', 'B*55:39', 'B*55:40', 'B*55:41', 'B*55:42',
                 'B*55:43', 'B*56:01', 'B*56:02', 'B*56:03', 'B*56:04', 'B*56:05', 'B*56:06', 'B*56:07', 'B*56:08',
                 'B*56:09', 'B*56:10', 'B*56:11', 'B*56:12', 'B*56:13', 'B*56:14', 'B*56:15', 'B*56:16', 'B*56:17',
                 'B*56:18', 'B*56:20', 'B*56:21', 'B*56:22', 'B*56:23', 'B*56:24', 'B*56:25', 'B*56:26', 'B*56:27',
                 'B*56:29', 'B*57:01', 'B*57:02', 'B*57:03', 'B*57:04', 'B*57:05', 'B*57:06', 'B*57:07', 'B*57:08',
                 'B*57:09', 'B*57:10', 'B*57:11', 'B*57:12', 'B*57:13', 'B*57:14', 'B*57:15', 'B*57:16', 'B*57:17',
                 'B*57:18', 'B*57:19', 'B*57:20', 'B*57:21', 'B*57:22', 'B*57:23', 'B*57:24', 'B*57:25', 'B*57:26',
                 'B*57:27', 'B*57:29', 'B*57:30', 'B*57:31', 'B*57:32', 'B*58:01', 'B*58:02', 'B*58:04', 'B*58:05',
                 'B*58:06', 'B*58:07', 'B*58:08', 'B*58:09', 'B*58:11', 'B*58:12', 'B*58:13', 'B*58:14', 'B*58:15',
                 'B*58:16', 'B*58:18', 'B*58:19', 'B*58:20', 'B*58:21', 'B*58:22', 'B*58:23', 'B*58:24', 'B*58:25',
                 'B*58:26', 'B*58:27', 'B*58:28', 'B*58:29', 'B*58:30', 'B*59:01', 'B*59:02', 'B*59:03', 'B*59:04',
                 'B*59:05', 'B*67:01', 'B*67:02', 'B*73:01', 'B*73:02', 'B*78:01', 'B*78:02', 'B*78:03', 'B*78:04',
                 'B*78:05', 'B*78:06', 'B*78:07', 'B*81:01', 'B*81:02', 'B*81:03', 'B*81:05', 'B*82:01', 'B*82:02',
                 'B*82:03', 'B*83:01', 'C*01:02', 'C*01:03', 'C*01:04', 'C*01:05', 'C*01:06', 'C*01:07', 'C*01:08',
                 'C*01:09', 'C*01:10', 'C*01:11', 'C*01:12', 'C*01:13', 'C*01:14', 'C*01:15', 'C*01:16', 'C*01:17',
                 'C*01:18', 'C*01:19', 'C*01:20', 'C*01:21', 'C*01:22', 'C*01:23', 'C*01:24', 'C*01:25', 'C*01:26',
                 'C*01:27', 'C*01:28', 'C*01:29', 'C*01:30', 'C*01:31', 'C*01:32', 'C*01:33', 'C*01:34', 'C*01:35',
                 'C*01:36', 'C*01:38', 'C*01:39', 'C*01:40', 'C*02:02', 'C*02:03', 'C*02:04', 'C*02:05', 'C*02:06',
                 'C*02:07', 'C*02:08', 'C*02:09', 'C*02:10', 'C*02:11', 'C*02:12', 'C*02:13', 'C*02:14', 'C*02:15',
                 'C*02:16', 'C*02:17', 'C*02:18', 'C*02:19', 'C*02:20', 'C*02:21', 'C*02:22', 'C*02:23', 'C*02:24',
                 'C*02:26', 'C*02:27', 'C*02:28', 'C*02:29', 'C*02:30', 'C*02:31', 'C*02:32', 'C*02:33', 'C*02:34',
                 'C*02:35', 'C*02:36', 'C*02:37', 'C*02:39', 'C*02:40', 'C*03:01', 'C*03:02', 'C*03:03', 'C*03:04',
                 'C*03:05', 'C*03:06', 'C*03:07', 'C*03:08', 'C*03:09', 'C*03:10', 'C*03:11', 'C*03:12', 'C*03:13',
                 'C*03:14', 'C*03:15', 'C*03:16', 'C*03:17', 'C*03:18', 'C*03:19', 'C*03:21', 'C*03:23', 'C*03:24',
                 'C*03:25', 'C*03:26', 'C*03:27', 'C*03:28', 'C*03:29', 'C*03:30', 'C*03:31', 'C*03:32', 'C*03:33',
                 'C*03:34', 'C*03:35', 'C*03:36', 'C*03:37', 'C*03:38', 'C*03:39', 'C*03:40', 'C*03:41', 'C*03:42',
                 'C*03:43', 'C*03:44', 'C*03:45', 'C*03:46', 'C*03:47', 'C*03:48', 'C*03:49', 'C*03:50', 'C*03:51',
                 'C*03:52', 'C*03:53', 'C*03:54', 'C*03:55', 'C*03:56', 'C*03:57', 'C*03:58', 'C*03:59', 'C*03:60',
                 'C*03:61', 'C*03:62', 'C*03:63', 'C*03:64', 'C*03:65', 'C*03:66', 'C*03:67', 'C*03:68', 'C*03:69',
                 'C*03:70', 'C*03:71', 'C*03:72', 'C*03:73', 'C*03:74', 'C*03:75', 'C*03:76', 'C*03:77', 'C*03:78',
                 'C*03:79', 'C*03:80', 'C*03:81', 'C*03:82', 'C*03:83', 'C*03:84', 'C*03:85', 'C*03:86', 'C*03:87',
                 'C*03:88', 'C*03:89', 'C*03:90', 'C*03:91', 'C*03:92', 'C*03:93', 'C*03:94', 'C*04:01', 'C*04:03',
                 'C*04:04', 'C*04:05', 'C*04:06', 'C*04:07', 'C*04:08', 'C*04:10', 'C*04:11', 'C*04:12', 'C*04:13',
                 'C*04:14', 'C*04:15', 'C*04:16', 'C*04:17', 'C*04:18', 'C*04:19', 'C*04:20', 'C*04:23', 'C*04:24',
                 'C*04:25', 'C*04:26', 'C*04:27', 'C*04:28', 'C*04:29', 'C*04:30', 'C*04:31', 'C*04:32', 'C*04:33',
                 'C*04:34', 'C*04:35', 'C*04:36', 'C*04:37', 'C*04:38', 'C*04:39', 'C*04:40', 'C*04:41', 'C*04:42',
                 'C*04:43', 'C*04:44', 'C*04:45', 'C*04:46', 'C*04:47', 'C*04:48', 'C*04:49', 'C*04:50', 'C*04:51',
                 'C*04:52', 'C*04:53', 'C*04:54', 'C*04:55', 'C*04:56', 'C*04:57', 'C*04:58', 'C*04:60', 'C*04:61',
                 'C*04:62', 'C*04:63', 'C*04:64', 'C*04:65', 'C*04:66', 'C*04:67', 'C*04:68', 'C*04:69', 'C*04:70',
                 'C*05:01', 'C*05:03', 'C*05:04', 'C*05:05', 'C*05:06', 'C*05:08', 'C*05:09', 'C*05:10', 'C*05:11',
                 'C*05:12', 'C*05:13', 'C*05:14', 'C*05:15', 'C*05:16', 'C*05:17', 'C*05:18', 'C*05:19', 'C*05:20',
                 'C*05:21', 'C*05:22', 'C*05:23', 'C*05:24', 'C*05:25', 'C*05:26', 'C*05:27', 'C*05:28', 'C*05:29',
                 'C*05:30', 'C*05:31', 'C*05:32', 'C*05:33', 'C*05:34', 'C*05:35', 'C*05:36', 'C*05:37', 'C*05:38',
                 'C*05:39', 'C*05:40', 'C*05:41', 'C*05:42', 'C*05:43', 'C*05:44', 'C*05:45', 'C*06:02', 'C*06:03',
                 'C*06:04', 'C*06:05', 'C*06:06', 'C*06:07', 'C*06:08', 'C*06:09', 'C*06:10', 'C*06:11', 'C*06:12',
                 'C*06:13', 'C*06:14', 'C*06:15', 'C*06:17', 'C*06:18', 'C*06:19', 'C*06:20', 'C*06:21', 'C*06:22',
                 'C*06:23', 'C*06:24', 'C*06:25', 'C*06:26', 'C*06:27', 'C*06:28', 'C*06:29', 'C*06:30', 'C*06:31',
                 'C*06:32', 'C*06:33', 'C*06:34', 'C*06:35', 'C*06:36', 'C*06:37', 'C*06:38', 'C*06:39', 'C*06:40',
                 'C*06:41', 'C*06:42', 'C*06:43', 'C*06:44', 'C*06:45', 'C*07:01', 'C*07:02', 'C*07:03', 'C*07:04',
                 'C*07:05', 'C*07:06', 'C*07:07', 'C*07:08', 'C*07:09', 'C*07:10', 'C*07:100', 'C*07:101', 'C*07:102',
                 'C*07:103', 'C*07:105', 'C*07:106', 'C*07:107', 'C*07:108', 'C*07:109', 'C*07:11', 'C*07:110',
                 'C*07:111', 'C*07:112', 'C*07:113', 'C*07:114', 'C*07:115', 'C*07:116', 'C*07:117', 'C*07:118',
                 'C*07:119', 'C*07:12', 'C*07:120', 'C*07:122', 'C*07:123', 'C*07:124', 'C*07:125', 'C*07:126',
                 'C*07:127', 'C*07:128', 'C*07:129', 'C*07:13', 'C*07:130', 'C*07:131', 'C*07:132', 'C*07:133',
                 'C*07:134', 'C*07:135', 'C*07:136', 'C*07:137', 'C*07:138', 'C*07:139', 'C*07:14', 'C*07:140',
                 'C*07:141', 'C*07:142', 'C*07:143', 'C*07:144', 'C*07:145', 'C*07:146', 'C*07:147', 'C*07:148',
                 'C*07:149', 'C*07:15', 'C*07:16', 'C*07:17', 'C*07:18', 'C*07:19', 'C*07:20', 'C*07:21', 'C*07:22',
                 'C*07:23', 'C*07:24', 'C*07:25', 'C*07:26', 'C*07:27', 'C*07:28', 'C*07:29', 'C*07:30', 'C*07:31',
                 'C*07:35', 'C*07:36', 'C*07:37', 'C*07:38', 'C*07:39', 'C*07:40', 'C*07:41', 'C*07:42', 'C*07:43',
                 'C*07:44', 'C*07:45', 'C*07:46', 'C*07:47', 'C*07:48', 'C*07:49', 'C*07:50', 'C*07:51', 'C*07:52',
                 'C*07:53', 'C*07:54', 'C*07:56', 'C*07:57', 'C*07:58', 'C*07:59', 'C*07:60', 'C*07:62', 'C*07:63',
                 'C*07:64', 'C*07:65', 'C*07:66', 'C*07:67', 'C*07:68', 'C*07:69', 'C*07:70', 'C*07:71', 'C*07:72',
                 'C*07:73', 'C*07:74', 'C*07:75', 'C*07:76', 'C*07:77', 'C*07:78', 'C*07:79', 'C*07:80', 'C*07:81',
                 'C*07:82', 'C*07:83', 'C*07:84', 'C*07:85', 'C*07:86', 'C*07:87', 'C*07:88', 'C*07:89', 'C*07:90',
                 'C*07:91', 'C*07:92', 'C*07:93', 'C*07:94', 'C*07:95', 'C*07:96', 'C*07:97', 'C*07:99', 'C*08:01',
                 'C*08:02', 'C*08:03', 'C*08:04', 'C*08:05', 'C*08:06', 'C*08:07', 'C*08:08', 'C*08:09', 'C*08:10',
                 'C*08:11', 'C*08:12', 'C*08:13', 'C*08:14', 'C*08:15', 'C*08:16', 'C*08:17', 'C*08:18', 'C*08:19',
                 'C*08:20', 'C*08:21', 'C*08:22', 'C*08:23', 'C*08:24', 'C*08:25', 'C*08:27', 'C*08:28', 'C*08:29',
                 'C*08:30', 'C*08:31', 'C*08:32', 'C*08:33', 'C*08:34', 'C*08:35', 'C*12:02', 'C*12:03', 'C*12:04',
                 'C*12:05', 'C*12:06', 'C*12:07', 'C*12:08', 'C*12:09', 'C*12:10', 'C*12:11', 'C*12:12', 'C*12:13',
                 'C*12:14', 'C*12:15', 'C*12:16', 'C*12:17', 'C*12:18', 'C*12:19', 'C*12:20', 'C*12:21', 'C*12:22',
                 'C*12:23', 'C*12:24', 'C*12:25', 'C*12:26', 'C*12:27', 'C*12:28', 'C*12:29', 'C*12:30', 'C*12:31',
                 'C*12:32', 'C*12:33', 'C*12:34', 'C*12:35', 'C*12:36', 'C*12:37', 'C*12:38', 'C*12:40', 'C*12:41',
                 'C*12:43', 'C*12:44', 'C*14:02', 'C*14:03', 'C*14:04', 'C*14:05', 'C*14:06', 'C*14:08', 'C*14:09',
                 'C*14:10', 'C*14:11', 'C*14:12', 'C*14:13', 'C*14:14', 'C*14:15', 'C*14:16', 'C*14:17', 'C*14:18',
                 'C*14:19', 'C*14:20', 'C*15:02', 'C*15:03', 'C*15:04', 'C*15:05', 'C*15:06', 'C*15:07', 'C*15:08',
                 'C*15:09', 'C*15:10', 'C*15:11', 'C*15:12', 'C*15:13', 'C*15:15', 'C*15:16', 'C*15:17', 'C*15:18',
                 'C*15:19', 'C*15:20', 'C*15:21', 'C*15:22', 'C*15:23', 'C*15:24', 'C*15:25', 'C*15:26', 'C*15:27',
                 'C*15:28', 'C*15:29', 'C*15:30', 'C*15:31', 'C*15:33', 'C*15:34', 'C*15:35', 'C*16:01', 'C*16:02',
                 'C*16:04', 'C*16:06', 'C*16:07', 'C*16:08', 'C*16:09', 'C*16:10', 'C*16:11', 'C*16:12', 'C*16:13',
                 'C*16:14', 'C*16:15', 'C*16:17', 'C*16:18', 'C*16:19', 'C*16:20', 'C*16:21', 'C*16:22', 'C*16:23',
                 'C*16:24', 'C*16:25', 'C*16:26', 'C*17:01', 'C*17:02', 'C*17:03', 'C*17:04', 'C*17:05', 'C*17:06',
                 'C*17:07', 'C*18:01', 'C*18:02', 'C*18:03', 'E*01:01', 'G*01:01', 'G*01:02', 'G*01:03', 'G*01:04',
                 'G*01:06', 'G*01:07', 'G*01:08', 'G*01:09'])
    __version = "2.4"

    @property
    def version(self):
        return self.__version

    def convert_alleles(self, alleles):
        return ["HLA-%s%s:%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    def parse_external_result(self, _file):
        result = defaultdict(dict)
        with open(_file, "r") as f:
            f = csv.reader(f, delimiter='\t')
            alleles = f.next()[3:-1]
            ic_pos = 3
            for row in f:
                pep_seq = row[1]
                for i, a in enumerate(alleles):
                    result[a][pep_seq] = float(row[ic_pos + i])
        return result

    def get_external_version(self, path=None):
        #can not be determined netmhcpan does not support --version or similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(_input))


class NetMHCpan_2_8(AExternalEpitopePrediction):
    """
    Implements the NetMHC binding (in current form for netMHCpan 2.8)

    Supported  MHC alleles currently only restricted to HLA alleles

    Nielsen, Morten, et al. "NetMHCpan, a method for quantitative predictions of peptide binding to any HLA-A and-B locus
    protein of known sequence." PloS one 2.8 (2007): e796.
    """
    __version = "2.8"
    __supported_length = frozenset([8, 9, 10, 11])
    __name = "netmhcpan"
    __command = "netMHCpan -p {peptides} -a {alleles} {options} -ic50 -xls -xlsfile {out}"
    __alleles = frozenset(['A*01:01', 'A*01:02', 'A*01:03', 'A*01:06', 'A*01:07', 'A*01:08', 'A*01:09', 'A*01:10', 'A*01:12',
                 'A*01:13', 'A*01:14', 'A*01:17', 'A*01:19', 'A*01:20', 'A*01:21', 'A*01:23', 'A*01:24', 'A*01:25',
                 'A*01:26', 'A*01:28', 'A*01:29', 'A*01:30', 'A*01:32', 'A*01:33', 'A*01:35', 'A*01:36', 'A*01:37',
                 'A*01:38', 'A*01:39', 'A*01:40', 'A*01:41', 'A*01:42', 'A*01:43', 'A*01:44', 'A*01:45', 'A*01:46',
                 'A*01:47', 'A*01:48', 'A*01:49', 'A*01:50', 'A*01:51', 'A*01:54', 'A*01:55', 'A*01:58', 'A*01:59',
                 'A*01:60', 'A*01:61', 'A*01:62', 'A*01:63', 'A*01:64', 'A*01:65', 'A*01:66', 'A*02:01', 'A*02:02',
                 'A*02:03', 'A*02:04', 'A*02:05', 'A*02:06', 'A*02:07', 'A*02:08', 'A*02:09', 'A*02:10', 'A*02:101',
                 'A*02:102', 'A*02:103', 'A*02:104', 'A*02:105', 'A*02:106', 'A*02:107', 'A*02:108', 'A*02:109',
                 'A*02:11', 'A*02:110', 'A*02:111', 'A*02:112', 'A*02:114', 'A*02:115', 'A*02:116', 'A*02:117',
                 'A*02:118', 'A*02:119', 'A*02:12', 'A*02:120', 'A*02:121', 'A*02:122', 'A*02:123', 'A*02:124',
                 'A*02:126', 'A*02:127', 'A*02:128', 'A*02:129', 'A*02:13', 'A*02:130', 'A*02:131', 'A*02:132',
                 'A*02:133', 'A*02:134', 'A*02:135', 'A*02:136', 'A*02:137', 'A*02:138', 'A*02:139', 'A*02:14',
                 'A*02:140', 'A*02:141', 'A*02:142', 'A*02:143', 'A*02:144', 'A*02:145', 'A*02:146', 'A*02:147',
                 'A*02:148', 'A*02:149', 'A*02:150', 'A*02:151', 'A*02:152', 'A*02:153', 'A*02:154', 'A*02:155',
                 'A*02:156', 'A*02:157', 'A*02:158', 'A*02:159', 'A*02:16', 'A*02:160', 'A*02:161', 'A*02:162',
                 'A*02:163', 'A*02:164', 'A*02:165', 'A*02:166', 'A*02:167', 'A*02:168', 'A*02:169', 'A*02:17',
                 'A*02:170', 'A*02:171', 'A*02:172', 'A*02:173', 'A*02:174', 'A*02:175', 'A*02:176', 'A*02:177',
                 'A*02:178', 'A*02:179', 'A*02:18', 'A*02:180', 'A*02:181', 'A*02:182', 'A*02:183', 'A*02:184',
                 'A*02:185', 'A*02:186', 'A*02:187', 'A*02:188', 'A*02:189', 'A*02:19', 'A*02:190', 'A*02:191',
                 'A*02:192', 'A*02:193', 'A*02:194', 'A*02:195', 'A*02:196', 'A*02:197', 'A*02:198', 'A*02:199',
                 'A*02:20', 'A*02:200', 'A*02:201', 'A*02:202', 'A*02:203', 'A*02:204', 'A*02:205', 'A*02:206',
                 'A*02:207', 'A*02:208', 'A*02:209', 'A*02:21', 'A*02:210', 'A*02:211', 'A*02:212', 'A*02:213',
                 'A*02:214', 'A*02:215', 'A*02:216', 'A*02:217', 'A*02:218', 'A*02:219', 'A*02:22', 'A*02:220',
                 'A*02:221', 'A*02:224', 'A*02:228', 'A*02:229', 'A*02:230', 'A*02:231', 'A*02:232', 'A*02:233',
                 'A*02:234', 'A*02:235', 'A*02:236', 'A*02:237', 'A*02:238', 'A*02:239', 'A*02:24', 'A*02:240',
                 'A*02:241', 'A*02:242', 'A*02:243', 'A*02:244', 'A*02:245', 'A*02:246', 'A*02:247', 'A*02:248',
                 'A*02:249', 'A*02:25', 'A*02:251', 'A*02:252', 'A*02:253', 'A*02:254', 'A*02:255', 'A*02:256',
                 'A*02:257', 'A*02:258', 'A*02:259', 'A*02:26', 'A*02:260', 'A*02:261', 'A*02:262', 'A*02:263',
                 'A*02:264', 'A*02:265', 'A*02:266', 'A*02:27', 'A*02:28', 'A*02:29', 'A*02:30', 'A*02:31', 'A*02:33',
                 'A*02:34', 'A*02:35', 'A*02:36', 'A*02:37', 'A*02:38', 'A*02:39', 'A*02:40', 'A*02:41', 'A*02:42',
                 'A*02:44', 'A*02:45', 'A*02:46', 'A*02:47', 'A*02:48', 'A*02:49', 'A*02:50', 'A*02:51', 'A*02:52',
                 'A*02:54', 'A*02:55', 'A*02:56', 'A*02:57', 'A*02:58', 'A*02:59', 'A*02:60', 'A*02:61', 'A*02:62',
                 'A*02:63', 'A*02:64', 'A*02:65', 'A*02:66', 'A*02:67', 'A*02:68', 'A*02:69', 'A*02:70', 'A*02:71',
                 'A*02:72', 'A*02:73', 'A*02:74', 'A*02:75', 'A*02:76', 'A*02:77', 'A*02:78', 'A*02:79', 'A*02:80',
                 'A*02:81', 'A*02:84', 'A*02:85', 'A*02:86', 'A*02:87', 'A*02:89', 'A*02:90', 'A*02:91', 'A*02:92',
                 'A*02:93', 'A*02:95', 'A*02:96', 'A*02:97', 'A*02:99', 'A*03:01', 'A*03:02', 'A*03:04', 'A*03:05',
                 'A*03:06', 'A*03:07', 'A*03:08', 'A*03:09', 'A*03:10', 'A*03:12', 'A*03:13', 'A*03:14', 'A*03:15',
                 'A*03:16', 'A*03:17', 'A*03:18', 'A*03:19', 'A*03:20', 'A*03:22', 'A*03:23', 'A*03:24', 'A*03:25',
                 'A*03:26', 'A*03:27', 'A*03:28', 'A*03:29', 'A*03:30', 'A*03:31', 'A*03:32', 'A*03:33', 'A*03:34',
                 'A*03:35', 'A*03:37', 'A*03:38', 'A*03:39', 'A*03:40', 'A*03:41', 'A*03:42', 'A*03:43', 'A*03:44',
                 'A*03:45', 'A*03:46', 'A*03:47', 'A*03:48', 'A*03:49', 'A*03:50', 'A*03:51', 'A*03:52', 'A*03:53',
                 'A*03:54', 'A*03:55', 'A*03:56', 'A*03:57', 'A*03:58', 'A*03:59', 'A*03:60', 'A*03:61', 'A*03:62',
                 'A*03:63', 'A*03:64', 'A*03:65', 'A*03:66', 'A*03:67', 'A*03:70', 'A*03:71', 'A*03:72', 'A*03:73',
                 'A*03:74', 'A*03:75', 'A*03:76', 'A*03:77', 'A*03:78', 'A*03:79', 'A*03:80', 'A*03:81', 'A*03:82',
                 'A*11:01', 'A*11:02', 'A*11:03', 'A*11:04', 'A*11:05', 'A*11:06', 'A*11:07', 'A*11:08', 'A*11:09',
                 'A*11:10', 'A*11:11', 'A*11:12', 'A*11:13', 'A*11:14', 'A*11:15', 'A*11:16', 'A*11:17', 'A*11:18',
                 'A*11:19', 'A*11:20', 'A*11:22', 'A*11:23', 'A*11:24', 'A*11:25', 'A*11:26', 'A*11:27', 'A*11:29',
                 'A*11:30', 'A*11:31', 'A*11:32', 'A*11:33', 'A*11:34', 'A*11:35', 'A*11:36', 'A*11:37', 'A*11:38',
                 'A*11:39', 'A*11:40', 'A*11:41', 'A*11:42', 'A*11:43', 'A*11:44', 'A*11:45', 'A*11:46', 'A*11:47',
                 'A*11:48', 'A*11:49', 'A*11:51', 'A*11:53', 'A*11:54', 'A*11:55', 'A*11:56', 'A*11:57', 'A*11:58',
                 'A*11:59', 'A*11:60', 'A*11:61', 'A*11:62', 'A*11:63', 'A*11:64', 'A*23:01', 'A*23:02', 'A*23:03',
                 'A*23:04', 'A*23:05', 'A*23:06', 'A*23:09', 'A*23:10', 'A*23:12', 'A*23:13', 'A*23:14', 'A*23:15',
                 'A*23:16', 'A*23:17', 'A*23:18', 'A*23:20', 'A*23:21', 'A*23:22', 'A*23:23', 'A*23:24', 'A*23:25',
                 'A*23:26', 'A*24:02', 'A*24:03', 'A*24:04', 'A*24:05', 'A*24:06', 'A*24:07', 'A*24:08', 'A*24:10',
                 'A*24:100', 'A*24:101', 'A*24:102', 'A*24:103', 'A*24:104', 'A*24:105', 'A*24:106', 'A*24:107',
                 'A*24:108', 'A*24:109', 'A*24:110', 'A*24:111', 'A*24:112', 'A*24:113', 'A*24:114', 'A*24:115',
                 'A*24:116', 'A*24:117', 'A*24:118', 'A*24:119', 'A*24:120', 'A*24:121', 'A*24:122', 'A*24:123',
                 'A*24:124', 'A*24:125', 'A*24:126', 'A*24:127', 'A*24:128', 'A*24:129', 'A*24:13', 'A*24:130',
                 'A*24:131', 'A*24:133', 'A*24:134', 'A*24:135', 'A*24:136', 'A*24:137', 'A*24:138', 'A*24:139',
                 'A*24:14', 'A*24:140', 'A*24:141', 'A*24:142', 'A*24:143', 'A*24:144', 'A*24:15', 'A*24:17', 'A*24:18',
                 'A*24:19', 'A*24:20', 'A*24:21', 'A*24:22', 'A*24:23', 'A*24:24', 'A*24:25', 'A*24:26', 'A*24:27',
                 'A*24:28', 'A*24:29', 'A*24:30', 'A*24:31', 'A*24:32', 'A*24:33', 'A*24:34', 'A*24:35', 'A*24:37',
                 'A*24:38', 'A*24:39', 'A*24:41', 'A*24:42', 'A*24:43', 'A*24:44', 'A*24:46', 'A*24:47', 'A*24:49',
                 'A*24:50', 'A*24:51', 'A*24:52', 'A*24:53', 'A*24:54', 'A*24:55', 'A*24:56', 'A*24:57', 'A*24:58',
                 'A*24:59', 'A*24:61', 'A*24:62', 'A*24:63', 'A*24:64', 'A*24:66', 'A*24:67', 'A*24:68', 'A*24:69',
                 'A*24:70', 'A*24:71', 'A*24:72', 'A*24:73', 'A*24:74', 'A*24:75', 'A*24:76', 'A*24:77', 'A*24:78',
                 'A*24:79', 'A*24:80', 'A*24:81', 'A*24:82', 'A*24:85', 'A*24:87', 'A*24:88', 'A*24:89', 'A*24:91',
                 'A*24:92', 'A*24:93', 'A*24:94', 'A*24:95', 'A*24:96', 'A*24:97', 'A*24:98', 'A*24:99', 'A*25:01',
                 'A*25:02', 'A*25:03', 'A*25:04', 'A*25:05', 'A*25:06', 'A*25:07', 'A*25:08', 'A*25:09', 'A*25:10',
                 'A*25:11', 'A*25:13', 'A*26:01', 'A*26:02', 'A*26:03', 'A*26:04', 'A*26:05', 'A*26:06', 'A*26:07',
                 'A*26:08', 'A*26:09', 'A*26:10', 'A*26:12', 'A*26:13', 'A*26:14', 'A*26:15', 'A*26:16', 'A*26:17',
                 'A*26:18', 'A*26:19', 'A*26:20', 'A*26:21', 'A*26:22', 'A*26:23', 'A*26:24', 'A*26:26', 'A*26:27',
                 'A*26:28', 'A*26:29', 'A*26:30', 'A*26:31', 'A*26:32', 'A*26:33', 'A*26:34', 'A*26:35', 'A*26:36',
                 'A*26:37', 'A*26:38', 'A*26:39', 'A*26:40', 'A*26:41', 'A*26:42', 'A*26:43', 'A*26:45', 'A*26:46',
                 'A*26:47', 'A*26:48', 'A*26:49', 'A*26:50', 'A*29:01', 'A*29:02', 'A*29:03', 'A*29:04', 'A*29:05',
                 'A*29:06', 'A*29:07', 'A*29:09', 'A*29:10', 'A*29:11', 'A*29:12', 'A*29:13', 'A*29:14', 'A*29:15',
                 'A*29:16', 'A*29:17', 'A*29:18', 'A*29:19', 'A*29:20', 'A*29:21', 'A*29:22', 'A*30:01', 'A*30:02',
                 'A*30:03', 'A*30:04', 'A*30:06', 'A*30:07', 'A*30:08', 'A*30:09', 'A*30:10', 'A*30:11', 'A*30:12',
                 'A*30:13', 'A*30:15', 'A*30:16', 'A*30:17', 'A*30:18', 'A*30:19', 'A*30:20', 'A*30:22', 'A*30:23',
                 'A*30:24', 'A*30:25', 'A*30:26', 'A*30:28', 'A*30:29', 'A*30:30', 'A*30:31', 'A*30:32', 'A*30:33',
                 'A*30:34', 'A*30:35', 'A*30:36', 'A*30:37', 'A*30:38', 'A*30:39', 'A*30:40', 'A*30:41', 'A*31:01',
                 'A*31:02', 'A*31:03', 'A*31:04', 'A*31:05', 'A*31:06', 'A*31:07', 'A*31:08', 'A*31:09', 'A*31:10',
                 'A*31:11', 'A*31:12', 'A*31:13', 'A*31:15', 'A*31:16', 'A*31:17', 'A*31:18', 'A*31:19', 'A*31:20',
                 'A*31:21', 'A*31:22', 'A*31:23', 'A*31:24', 'A*31:25', 'A*31:26', 'A*31:27', 'A*31:28', 'A*31:29',
                 'A*31:30', 'A*31:31', 'A*31:32', 'A*31:33', 'A*31:34', 'A*31:35', 'A*31:36', 'A*31:37', 'A*32:01',
                 'A*32:02', 'A*32:03', 'A*32:04', 'A*32:05', 'A*32:06', 'A*32:07', 'A*32:08', 'A*32:09', 'A*32:10',
                 'A*32:12', 'A*32:13', 'A*32:14', 'A*32:15', 'A*32:16', 'A*32:17', 'A*32:18', 'A*32:20', 'A*32:21',
                 'A*32:22', 'A*32:23', 'A*32:24', 'A*32:25', 'A*33:01', 'A*33:03', 'A*33:04', 'A*33:05', 'A*33:06',
                 'A*33:07', 'A*33:08', 'A*33:09', 'A*33:10', 'A*33:11', 'A*33:12', 'A*33:13', 'A*33:14', 'A*33:15',
                 'A*33:16', 'A*33:17', 'A*33:18', 'A*33:19', 'A*33:20', 'A*33:21', 'A*33:22', 'A*33:23', 'A*33:24',
                 'A*33:25', 'A*33:26', 'A*33:27', 'A*33:28', 'A*33:29', 'A*33:30', 'A*33:31', 'A*34:01', 'A*34:02',
                 'A*34:03', 'A*34:04', 'A*34:05', 'A*34:06', 'A*34:07', 'A*34:08', 'A*36:01', 'A*36:02', 'A*36:03',
                 'A*36:04', 'A*36:05', 'A*43:01', 'A*66:01', 'A*66:02', 'A*66:03', 'A*66:04', 'A*66:05', 'A*66:06',
                 'A*66:07', 'A*66:08', 'A*66:09', 'A*66:10', 'A*66:11', 'A*66:12', 'A*66:13', 'A*66:14', 'A*66:15',
                 'A*68:01', 'A*68:02', 'A*68:03', 'A*68:04', 'A*68:05', 'A*68:06', 'A*68:07', 'A*68:08', 'A*68:09',
                 'A*68:10', 'A*68:12', 'A*68:13', 'A*68:14', 'A*68:15', 'A*68:16', 'A*68:17', 'A*68:19', 'A*68:20',
                 'A*68:21', 'A*68:22', 'A*68:23', 'A*68:24', 'A*68:25', 'A*68:26', 'A*68:27', 'A*68:28', 'A*68:29',
                 'A*68:30', 'A*68:31', 'A*68:32', 'A*68:33', 'A*68:34', 'A*68:35', 'A*68:36', 'A*68:37', 'A*68:38',
                 'A*68:39', 'A*68:40', 'A*68:41', 'A*68:42', 'A*68:43', 'A*68:44', 'A*68:45', 'A*68:46', 'A*68:47',
                 'A*68:48', 'A*68:50', 'A*68:51', 'A*68:52', 'A*68:53', 'A*68:54', 'A*69:01', 'A*74:01', 'A*74:02',
                 'A*74:03', 'A*74:04', 'A*74:05', 'A*74:06', 'A*74:07', 'A*74:08', 'A*74:09', 'A*74:10', 'A*74:11',
                 'A*74:13', 'A*80:01', 'A*80:02', 'B*07:02', 'B*07:03', 'B*07:04', 'B*07:05', 'B*07:06', 'B*07:07',
                 'B*07:08', 'B*07:09', 'B*07:10', 'B*07:100', 'B*07:101', 'B*07:102', 'B*07:103', 'B*07:104',
                 'B*07:105', 'B*07:106', 'B*07:107', 'B*07:108', 'B*07:109', 'B*07:11', 'B*07:110', 'B*07:112',
                 'B*07:113', 'B*07:114', 'B*07:115', 'B*07:12', 'B*07:13', 'B*07:14', 'B*07:15', 'B*07:16', 'B*07:17',
                 'B*07:18', 'B*07:19', 'B*07:20', 'B*07:21', 'B*07:22', 'B*07:23', 'B*07:24', 'B*07:25', 'B*07:26',
                 'B*07:27', 'B*07:28', 'B*07:29', 'B*07:30', 'B*07:31', 'B*07:32', 'B*07:33', 'B*07:34', 'B*07:35',
                 'B*07:36', 'B*07:37', 'B*07:38', 'B*07:39', 'B*07:40', 'B*07:41', 'B*07:42', 'B*07:43', 'B*07:44',
                 'B*07:45', 'B*07:46', 'B*07:47', 'B*07:48', 'B*07:50', 'B*07:51', 'B*07:52', 'B*07:53', 'B*07:54',
                 'B*07:55', 'B*07:56', 'B*07:57', 'B*07:58', 'B*07:59', 'B*07:60', 'B*07:61', 'B*07:62', 'B*07:63',
                 'B*07:64', 'B*07:65', 'B*07:66', 'B*07:68', 'B*07:69', 'B*07:70', 'B*07:71', 'B*07:72', 'B*07:73',
                 'B*07:74', 'B*07:75', 'B*07:76', 'B*07:77', 'B*07:78', 'B*07:79', 'B*07:80', 'B*07:81', 'B*07:82',
                 'B*07:83', 'B*07:84', 'B*07:85', 'B*07:86', 'B*07:87', 'B*07:88', 'B*07:89', 'B*07:90', 'B*07:91',
                 'B*07:92', 'B*07:93', 'B*07:94', 'B*07:95', 'B*07:96', 'B*07:97', 'B*07:98', 'B*07:99', 'B*08:01',
                 'B*08:02', 'B*08:03', 'B*08:04', 'B*08:05', 'B*08:07', 'B*08:09', 'B*08:10', 'B*08:11', 'B*08:12',
                 'B*08:13', 'B*08:14', 'B*08:15', 'B*08:16', 'B*08:17', 'B*08:18', 'B*08:20', 'B*08:21', 'B*08:22',
                 'B*08:23', 'B*08:24', 'B*08:25', 'B*08:26', 'B*08:27', 'B*08:28', 'B*08:29', 'B*08:31', 'B*08:32',
                 'B*08:33', 'B*08:34', 'B*08:35', 'B*08:36', 'B*08:37', 'B*08:38', 'B*08:39', 'B*08:40', 'B*08:41',
                 'B*08:42', 'B*08:43', 'B*08:44', 'B*08:45', 'B*08:46', 'B*08:47', 'B*08:48', 'B*08:49', 'B*08:50',
                 'B*08:51', 'B*08:52', 'B*08:53', 'B*08:54', 'B*08:55', 'B*08:56', 'B*08:57', 'B*08:58', 'B*08:59',
                 'B*08:60', 'B*08:61', 'B*08:62', 'B*13:01', 'B*13:02', 'B*13:03', 'B*13:04', 'B*13:06', 'B*13:09',
                 'B*13:10', 'B*13:11', 'B*13:12', 'B*13:13', 'B*13:14', 'B*13:15', 'B*13:16', 'B*13:17', 'B*13:18',
                 'B*13:19', 'B*13:20', 'B*13:21', 'B*13:22', 'B*13:23', 'B*13:25', 'B*13:26', 'B*13:27', 'B*13:28',
                 'B*13:29', 'B*13:30', 'B*13:31', 'B*13:32', 'B*13:33', 'B*13:34', 'B*13:35', 'B*13:36', 'B*13:37',
                 'B*13:38', 'B*13:39', 'B*14:01', 'B*14:02', 'B*14:03', 'B*14:04', 'B*14:05', 'B*14:06', 'B*14:08',
                 'B*14:09', 'B*14:10', 'B*14:11', 'B*14:12', 'B*14:13', 'B*14:14', 'B*14:15', 'B*14:16', 'B*14:17',
                 'B*14:18', 'B*15:01', 'B*15:02', 'B*15:03', 'B*15:04', 'B*15:05', 'B*15:06', 'B*15:07', 'B*15:08',
                 'B*15:09', 'B*15:10', 'B*15:101', 'B*15:102', 'B*15:103', 'B*15:104', 'B*15:105', 'B*15:106',
                 'B*15:107', 'B*15:108', 'B*15:109', 'B*15:11', 'B*15:110', 'B*15:112', 'B*15:113', 'B*15:114',
                 'B*15:115', 'B*15:116', 'B*15:117', 'B*15:118', 'B*15:119', 'B*15:12', 'B*15:120', 'B*15:121',
                 'B*15:122', 'B*15:123', 'B*15:124', 'B*15:125', 'B*15:126', 'B*15:127', 'B*15:128', 'B*15:129',
                 'B*15:13', 'B*15:131', 'B*15:132', 'B*15:133', 'B*15:134', 'B*15:135', 'B*15:136', 'B*15:137',
                 'B*15:138', 'B*15:139', 'B*15:14', 'B*15:140', 'B*15:141', 'B*15:142', 'B*15:143', 'B*15:144',
                 'B*15:145', 'B*15:146', 'B*15:147', 'B*15:148', 'B*15:15', 'B*15:150', 'B*15:151', 'B*15:152',
                 'B*15:153', 'B*15:154', 'B*15:155', 'B*15:156', 'B*15:157', 'B*15:158', 'B*15:159', 'B*15:16',
                 'B*15:160', 'B*15:161', 'B*15:162', 'B*15:163', 'B*15:164', 'B*15:165', 'B*15:166', 'B*15:167',
                 'B*15:168', 'B*15:169', 'B*15:17', 'B*15:170', 'B*15:171', 'B*15:172', 'B*15:173', 'B*15:174',
                 'B*15:175', 'B*15:176', 'B*15:177', 'B*15:178', 'B*15:179', 'B*15:18', 'B*15:180', 'B*15:183',
                 'B*15:184', 'B*15:185', 'B*15:186', 'B*15:187', 'B*15:188', 'B*15:189', 'B*15:19', 'B*15:191',
                 'B*15:192', 'B*15:193', 'B*15:194', 'B*15:195', 'B*15:196', 'B*15:197', 'B*15:198', 'B*15:199',
                 'B*15:20', 'B*15:200', 'B*15:201', 'B*15:202', 'B*15:21', 'B*15:23', 'B*15:24', 'B*15:25', 'B*15:27',
                 'B*15:28', 'B*15:29', 'B*15:30', 'B*15:31', 'B*15:32', 'B*15:33', 'B*15:34', 'B*15:35', 'B*15:36',
                 'B*15:37', 'B*15:38', 'B*15:39', 'B*15:40', 'B*15:42', 'B*15:43', 'B*15:44', 'B*15:45', 'B*15:46',
                 'B*15:47', 'B*15:48', 'B*15:49', 'B*15:50', 'B*15:51', 'B*15:52', 'B*15:53', 'B*15:54', 'B*15:55',
                 'B*15:56', 'B*15:57', 'B*15:58', 'B*15:60', 'B*15:61', 'B*15:62', 'B*15:63', 'B*15:64', 'B*15:65',
                 'B*15:66', 'B*15:67', 'B*15:68', 'B*15:69', 'B*15:70', 'B*15:71', 'B*15:72', 'B*15:73', 'B*15:74',
                 'B*15:75', 'B*15:76', 'B*15:77', 'B*15:78', 'B*15:80', 'B*15:81', 'B*15:82', 'B*15:83', 'B*15:84',
                 'B*15:85', 'B*15:86', 'B*15:87', 'B*15:88', 'B*15:89', 'B*15:90', 'B*15:91', 'B*15:92', 'B*15:93',
                 'B*15:95', 'B*15:96', 'B*15:97', 'B*15:98', 'B*15:99', 'B*18:01', 'B*18:02', 'B*18:03', 'B*18:04',
                 'B*18:05', 'B*18:06', 'B*18:07', 'B*18:08', 'B*18:09', 'B*18:10', 'B*18:11', 'B*18:12', 'B*18:13',
                 'B*18:14', 'B*18:15', 'B*18:18', 'B*18:19', 'B*18:20', 'B*18:21', 'B*18:22', 'B*18:24', 'B*18:25',
                 'B*18:26', 'B*18:27', 'B*18:28', 'B*18:29', 'B*18:30', 'B*18:31', 'B*18:32', 'B*18:33', 'B*18:34',
                 'B*18:35', 'B*18:36', 'B*18:37', 'B*18:38', 'B*18:39', 'B*18:40', 'B*18:41', 'B*18:42', 'B*18:43',
                 'B*18:44', 'B*18:45', 'B*18:46', 'B*18:47', 'B*18:48', 'B*18:49', 'B*18:50', 'B*27:01', 'B*27:02',
                 'B*27:03', 'B*27:04', 'B*27:05', 'B*27:06', 'B*27:07', 'B*27:08', 'B*27:09', 'B*27:10', 'B*27:11',
                 'B*27:12', 'B*27:13', 'B*27:14', 'B*27:15', 'B*27:16', 'B*27:17', 'B*27:18', 'B*27:19', 'B*27:20',
                 'B*27:21', 'B*27:23', 'B*27:24', 'B*27:25', 'B*27:26', 'B*27:27', 'B*27:28', 'B*27:29', 'B*27:30',
                 'B*27:31', 'B*27:32', 'B*27:33', 'B*27:34', 'B*27:35', 'B*27:36', 'B*27:37', 'B*27:38', 'B*27:39',
                 'B*27:40', 'B*27:41', 'B*27:42', 'B*27:43', 'B*27:44', 'B*27:45', 'B*27:46', 'B*27:47', 'B*27:48',
                 'B*27:49', 'B*27:50', 'B*27:51', 'B*27:52', 'B*27:53', 'B*27:54', 'B*27:55', 'B*27:56', 'B*27:57',
                 'B*27:58', 'B*27:60', 'B*27:61', 'B*27:62', 'B*27:63', 'B*27:67', 'B*27:68', 'B*27:69', 'B*35:01',
                 'B*35:02', 'B*35:03', 'B*35:04', 'B*35:05', 'B*35:06', 'B*35:07', 'B*35:08', 'B*35:09', 'B*35:10',
                 'B*35:100', 'B*35:101', 'B*35:102', 'B*35:103', 'B*35:104', 'B*35:105', 'B*35:106', 'B*35:107',
                 'B*35:108', 'B*35:109', 'B*35:11', 'B*35:110', 'B*35:111', 'B*35:112', 'B*35:113', 'B*35:114',
                 'B*35:115', 'B*35:116', 'B*35:117', 'B*35:118', 'B*35:119', 'B*35:12', 'B*35:120', 'B*35:121',
                 'B*35:122', 'B*35:123', 'B*35:124', 'B*35:125', 'B*35:126', 'B*35:127', 'B*35:128', 'B*35:13',
                 'B*35:131', 'B*35:132', 'B*35:133', 'B*35:135', 'B*35:136', 'B*35:137', 'B*35:138', 'B*35:139',
                 'B*35:14', 'B*35:140', 'B*35:141', 'B*35:142', 'B*35:143', 'B*35:144', 'B*35:15', 'B*35:16', 'B*35:17',
                 'B*35:18', 'B*35:19', 'B*35:20', 'B*35:21', 'B*35:22', 'B*35:23', 'B*35:24', 'B*35:25', 'B*35:26',
                 'B*35:27', 'B*35:28', 'B*35:29', 'B*35:30', 'B*35:31', 'B*35:32', 'B*35:33', 'B*35:34', 'B*35:35',
                 'B*35:36', 'B*35:37', 'B*35:38', 'B*35:39', 'B*35:41', 'B*35:42', 'B*35:43', 'B*35:44', 'B*35:45',
                 'B*35:46', 'B*35:47', 'B*35:48', 'B*35:49', 'B*35:50', 'B*35:51', 'B*35:52', 'B*35:54', 'B*35:55',
                 'B*35:56', 'B*35:57', 'B*35:58', 'B*35:59', 'B*35:60', 'B*35:61', 'B*35:62', 'B*35:63', 'B*35:64',
                 'B*35:66', 'B*35:67', 'B*35:68', 'B*35:69', 'B*35:70', 'B*35:71', 'B*35:72', 'B*35:74', 'B*35:75',
                 'B*35:76', 'B*35:77', 'B*35:78', 'B*35:79', 'B*35:80', 'B*35:81', 'B*35:82', 'B*35:83', 'B*35:84',
                 'B*35:85', 'B*35:86', 'B*35:87', 'B*35:88', 'B*35:89', 'B*35:90', 'B*35:91', 'B*35:92', 'B*35:93',
                 'B*35:94', 'B*35:95', 'B*35:96', 'B*35:97', 'B*35:98', 'B*35:99', 'B*37:01', 'B*37:02', 'B*37:04',
                 'B*37:05', 'B*37:06', 'B*37:07', 'B*37:08', 'B*37:09', 'B*37:10', 'B*37:11', 'B*37:12', 'B*37:13',
                 'B*37:14', 'B*37:15', 'B*37:17', 'B*37:18', 'B*37:19', 'B*37:20', 'B*37:21', 'B*37:22', 'B*37:23',
                 'B*38:01', 'B*38:02', 'B*38:03', 'B*38:04', 'B*38:05', 'B*38:06', 'B*38:07', 'B*38:08', 'B*38:09',
                 'B*38:10', 'B*38:11', 'B*38:12', 'B*38:13', 'B*38:14', 'B*38:15', 'B*38:16', 'B*38:17', 'B*38:18',
                 'B*38:19', 'B*38:20', 'B*38:21', 'B*38:22', 'B*38:23', 'B*39:01', 'B*39:02', 'B*39:03', 'B*39:04',
                 'B*39:05', 'B*39:06', 'B*39:07', 'B*39:08', 'B*39:09', 'B*39:10', 'B*39:11', 'B*39:12', 'B*39:13',
                 'B*39:14', 'B*39:15', 'B*39:16', 'B*39:17', 'B*39:18', 'B*39:19', 'B*39:20', 'B*39:22', 'B*39:23',
                 'B*39:24', 'B*39:26', 'B*39:27', 'B*39:28', 'B*39:29', 'B*39:30', 'B*39:31', 'B*39:32', 'B*39:33',
                 'B*39:34', 'B*39:35', 'B*39:36', 'B*39:37', 'B*39:39', 'B*39:41', 'B*39:42', 'B*39:43', 'B*39:44',
                 'B*39:45', 'B*39:46', 'B*39:47', 'B*39:48', 'B*39:49', 'B*39:50', 'B*39:51', 'B*39:52', 'B*39:53',
                 'B*39:54', 'B*39:55', 'B*39:56', 'B*39:57', 'B*39:58', 'B*39:59', 'B*39:60', 'B*40:01', 'B*40:02',
                 'B*40:03', 'B*40:04', 'B*40:05', 'B*40:06', 'B*40:07', 'B*40:08', 'B*40:09', 'B*40:10', 'B*40:100',
                 'B*40:101', 'B*40:102', 'B*40:103', 'B*40:104', 'B*40:105', 'B*40:106', 'B*40:107', 'B*40:108',
                 'B*40:109', 'B*40:11', 'B*40:110', 'B*40:111', 'B*40:112', 'B*40:113', 'B*40:114', 'B*40:115',
                 'B*40:116', 'B*40:117', 'B*40:119', 'B*40:12', 'B*40:120', 'B*40:121', 'B*40:122', 'B*40:123',
                 'B*40:124', 'B*40:125', 'B*40:126', 'B*40:127', 'B*40:128', 'B*40:129', 'B*40:13', 'B*40:130',
                 'B*40:131', 'B*40:132', 'B*40:134', 'B*40:135', 'B*40:136', 'B*40:137', 'B*40:138', 'B*40:139',
                 'B*40:14', 'B*40:140', 'B*40:141', 'B*40:143', 'B*40:145', 'B*40:146', 'B*40:147', 'B*40:15',
                 'B*40:16', 'B*40:18', 'B*40:19', 'B*40:20', 'B*40:21', 'B*40:23', 'B*40:24', 'B*40:25', 'B*40:26',
                 'B*40:27', 'B*40:28', 'B*40:29', 'B*40:30', 'B*40:31', 'B*40:32', 'B*40:33', 'B*40:34', 'B*40:35',
                 'B*40:36', 'B*40:37', 'B*40:38', 'B*40:39', 'B*40:40', 'B*40:42', 'B*40:43', 'B*40:44', 'B*40:45',
                 'B*40:46', 'B*40:47', 'B*40:48', 'B*40:49', 'B*40:50', 'B*40:51', 'B*40:52', 'B*40:53', 'B*40:54',
                 'B*40:55', 'B*40:56', 'B*40:57', 'B*40:58', 'B*40:59', 'B*40:60', 'B*40:61', 'B*40:62', 'B*40:63',
                 'B*40:64', 'B*40:65', 'B*40:66', 'B*40:67', 'B*40:68', 'B*40:69', 'B*40:70', 'B*40:71', 'B*40:72',
                 'B*40:73', 'B*40:74', 'B*40:75', 'B*40:76', 'B*40:77', 'B*40:78', 'B*40:79', 'B*40:80', 'B*40:81',
                 'B*40:82', 'B*40:83', 'B*40:84', 'B*40:85', 'B*40:86', 'B*40:87', 'B*40:88', 'B*40:89', 'B*40:90',
                 'B*40:91', 'B*40:92', 'B*40:93', 'B*40:94', 'B*40:95', 'B*40:96', 'B*40:97', 'B*40:98', 'B*40:99',
                 'B*41:01', 'B*41:02', 'B*41:03', 'B*41:04', 'B*41:05', 'B*41:06', 'B*41:07', 'B*41:08', 'B*41:09',
                 'B*41:10', 'B*41:11', 'B*41:12', 'B*42:01', 'B*42:02', 'B*42:04', 'B*42:05', 'B*42:06', 'B*42:07',
                 'B*42:08', 'B*42:09', 'B*42:10', 'B*42:11', 'B*42:12', 'B*42:13', 'B*42:14', 'B*44:02', 'B*44:03',
                 'B*44:04', 'B*44:05', 'B*44:06', 'B*44:07', 'B*44:08', 'B*44:09', 'B*44:10', 'B*44:100', 'B*44:101',
                 'B*44:102', 'B*44:103', 'B*44:104', 'B*44:105', 'B*44:106', 'B*44:107', 'B*44:109', 'B*44:11',
                 'B*44:110', 'B*44:12', 'B*44:13', 'B*44:14', 'B*44:15', 'B*44:16', 'B*44:17', 'B*44:18', 'B*44:20',
                 'B*44:21', 'B*44:22', 'B*44:24', 'B*44:25', 'B*44:26', 'B*44:27', 'B*44:28', 'B*44:29', 'B*44:30',
                 'B*44:31', 'B*44:32', 'B*44:33', 'B*44:34', 'B*44:35', 'B*44:36', 'B*44:37', 'B*44:38', 'B*44:39',
                 'B*44:40', 'B*44:41', 'B*44:42', 'B*44:43', 'B*44:44', 'B*44:45', 'B*44:46', 'B*44:47', 'B*44:48',
                 'B*44:49', 'B*44:50', 'B*44:51', 'B*44:53', 'B*44:54', 'B*44:55', 'B*44:57', 'B*44:59', 'B*44:60',
                 'B*44:62', 'B*44:63', 'B*44:64', 'B*44:65', 'B*44:66', 'B*44:67', 'B*44:68', 'B*44:69', 'B*44:70',
                 'B*44:71', 'B*44:72', 'B*44:73', 'B*44:74', 'B*44:75', 'B*44:76', 'B*44:77', 'B*44:78', 'B*44:79',
                 'B*44:80', 'B*44:81', 'B*44:82', 'B*44:83', 'B*44:84', 'B*44:85', 'B*44:86', 'B*44:87', 'B*44:88',
                 'B*44:89', 'B*44:90', 'B*44:91', 'B*44:92', 'B*44:93', 'B*44:94', 'B*44:95', 'B*44:96', 'B*44:97',
                 'B*44:98', 'B*44:99', 'B*45:01', 'B*45:02', 'B*45:03', 'B*45:04', 'B*45:05', 'B*45:06', 'B*45:07',
                 'B*45:08', 'B*45:09', 'B*45:10', 'B*45:11', 'B*45:12', 'B*46:01', 'B*46:02', 'B*46:03', 'B*46:04',
                 'B*46:05', 'B*46:06', 'B*46:08', 'B*46:09', 'B*46:10', 'B*46:11', 'B*46:12', 'B*46:13', 'B*46:14',
                 'B*46:16', 'B*46:17', 'B*46:18', 'B*46:19', 'B*46:20', 'B*46:21', 'B*46:22', 'B*46:23', 'B*46:24',
                 'B*47:01', 'B*47:02', 'B*47:03', 'B*47:04', 'B*47:05', 'B*47:06', 'B*47:07', 'B*48:01', 'B*48:02',
                 'B*48:03', 'B*48:04', 'B*48:05', 'B*48:06', 'B*48:07', 'B*48:08', 'B*48:09', 'B*48:10', 'B*48:11',
                 'B*48:12', 'B*48:13', 'B*48:14', 'B*48:15', 'B*48:16', 'B*48:17', 'B*48:18', 'B*48:19', 'B*48:20',
                 'B*48:21', 'B*48:22', 'B*48:23', 'B*49:01', 'B*49:02', 'B*49:03', 'B*49:04', 'B*49:05', 'B*49:06',
                 'B*49:07', 'B*49:08', 'B*49:09', 'B*49:10', 'B*50:01', 'B*50:02', 'B*50:04', 'B*50:05', 'B*50:06',
                 'B*50:07', 'B*50:08', 'B*50:09', 'B*51:01', 'B*51:02', 'B*51:03', 'B*51:04', 'B*51:05', 'B*51:06',
                 'B*51:07', 'B*51:08', 'B*51:09', 'B*51:12', 'B*51:13', 'B*51:14', 'B*51:15', 'B*51:16', 'B*51:17',
                 'B*51:18', 'B*51:19', 'B*51:20', 'B*51:21', 'B*51:22', 'B*51:23', 'B*51:24', 'B*51:26', 'B*51:28',
                 'B*51:29', 'B*51:30', 'B*51:31', 'B*51:32', 'B*51:33', 'B*51:34', 'B*51:35', 'B*51:36', 'B*51:37',
                 'B*51:38', 'B*51:39', 'B*51:40', 'B*51:42', 'B*51:43', 'B*51:45', 'B*51:46', 'B*51:48', 'B*51:49',
                 'B*51:50', 'B*51:51', 'B*51:52', 'B*51:53', 'B*51:54', 'B*51:55', 'B*51:56', 'B*51:57', 'B*51:58',
                 'B*51:59', 'B*51:60', 'B*51:61', 'B*51:62', 'B*51:63', 'B*51:64', 'B*51:65', 'B*51:66', 'B*51:67',
                 'B*51:68', 'B*51:69', 'B*51:70', 'B*51:71', 'B*51:72', 'B*51:73', 'B*51:74', 'B*51:75', 'B*51:76',
                 'B*51:77', 'B*51:78', 'B*51:79', 'B*51:80', 'B*51:81', 'B*51:82', 'B*51:83', 'B*51:84', 'B*51:85',
                 'B*51:86', 'B*51:87', 'B*51:88', 'B*51:89', 'B*51:90', 'B*51:91', 'B*51:92', 'B*51:93', 'B*51:94',
                 'B*51:95', 'B*51:96', 'B*52:01', 'B*52:02', 'B*52:03', 'B*52:04', 'B*52:05', 'B*52:06', 'B*52:07',
                 'B*52:08', 'B*52:09', 'B*52:10', 'B*52:11', 'B*52:12', 'B*52:13', 'B*52:14', 'B*52:15', 'B*52:16',
                 'B*52:17', 'B*52:18', 'B*52:19', 'B*52:20', 'B*52:21', 'B*53:01', 'B*53:02', 'B*53:03', 'B*53:04',
                 'B*53:05', 'B*53:06', 'B*53:07', 'B*53:08', 'B*53:09', 'B*53:10', 'B*53:11', 'B*53:12', 'B*53:13',
                 'B*53:14', 'B*53:15', 'B*53:16', 'B*53:17', 'B*53:18', 'B*53:19', 'B*53:20', 'B*53:21', 'B*53:22',
                 'B*53:23', 'B*54:01', 'B*54:02', 'B*54:03', 'B*54:04', 'B*54:06', 'B*54:07', 'B*54:09', 'B*54:10',
                 'B*54:11', 'B*54:12', 'B*54:13', 'B*54:14', 'B*54:15', 'B*54:16', 'B*54:17', 'B*54:18', 'B*54:19',
                 'B*54:20', 'B*54:21', 'B*54:22', 'B*54:23', 'B*55:01', 'B*55:02', 'B*55:03', 'B*55:04', 'B*55:05',
                 'B*55:07', 'B*55:08', 'B*55:09', 'B*55:10', 'B*55:11', 'B*55:12', 'B*55:13', 'B*55:14', 'B*55:15',
                 'B*55:16', 'B*55:17', 'B*55:18', 'B*55:19', 'B*55:20', 'B*55:21', 'B*55:22', 'B*55:23', 'B*55:24',
                 'B*55:25', 'B*55:26', 'B*55:27', 'B*55:28', 'B*55:29', 'B*55:30', 'B*55:31', 'B*55:32', 'B*55:33',
                 'B*55:34', 'B*55:35', 'B*55:36', 'B*55:37', 'B*55:38', 'B*55:39', 'B*55:40', 'B*55:41', 'B*55:42',
                 'B*55:43', 'B*56:01', 'B*56:02', 'B*56:03', 'B*56:04', 'B*56:05', 'B*56:06', 'B*56:07', 'B*56:08',
                 'B*56:09', 'B*56:10', 'B*56:11', 'B*56:12', 'B*56:13', 'B*56:14', 'B*56:15', 'B*56:16', 'B*56:17',
                 'B*56:18', 'B*56:20', 'B*56:21', 'B*56:22', 'B*56:23', 'B*56:24', 'B*56:25', 'B*56:26', 'B*56:27',
                 'B*56:29', 'B*57:01', 'B*57:02', 'B*57:03', 'B*57:04', 'B*57:05', 'B*57:06', 'B*57:07', 'B*57:08',
                 'B*57:09', 'B*57:10', 'B*57:11', 'B*57:12', 'B*57:13', 'B*57:14', 'B*57:15', 'B*57:16', 'B*57:17',
                 'B*57:18', 'B*57:19', 'B*57:20', 'B*57:21', 'B*57:22', 'B*57:23', 'B*57:24', 'B*57:25', 'B*57:26',
                 'B*57:27', 'B*57:29', 'B*57:30', 'B*57:31', 'B*57:32', 'B*58:01', 'B*58:02', 'B*58:04', 'B*58:05',
                 'B*58:06', 'B*58:07', 'B*58:08', 'B*58:09', 'B*58:11', 'B*58:12', 'B*58:13', 'B*58:14', 'B*58:15',
                 'B*58:16', 'B*58:18', 'B*58:19', 'B*58:20', 'B*58:21', 'B*58:22', 'B*58:23', 'B*58:24', 'B*58:25',
                 'B*58:26', 'B*58:27', 'B*58:28', 'B*58:29', 'B*58:30', 'B*59:01', 'B*59:02', 'B*59:03', 'B*59:04',
                 'B*59:05', 'B*67:01', 'B*67:02', 'B*73:01', 'B*73:02', 'B*78:01', 'B*78:02', 'B*78:03', 'B*78:04',
                 'B*78:05', 'B*78:06', 'B*78:07', 'B*81:01', 'B*81:02', 'B*81:03', 'B*81:05', 'B*82:01', 'B*82:02',
                 'B*82:03', 'B*83:01', 'C*01:02', 'C*01:03', 'C*01:04', 'C*01:05', 'C*01:06', 'C*01:07', 'C*01:08',
                 'C*01:09', 'C*01:10', 'C*01:11', 'C*01:12', 'C*01:13', 'C*01:14', 'C*01:15', 'C*01:16', 'C*01:17',
                 'C*01:18', 'C*01:19', 'C*01:20', 'C*01:21', 'C*01:22', 'C*01:23', 'C*01:24', 'C*01:25', 'C*01:26',
                 'C*01:27', 'C*01:28', 'C*01:29', 'C*01:30', 'C*01:31', 'C*01:32', 'C*01:33', 'C*01:34', 'C*01:35',
                 'C*01:36', 'C*01:38', 'C*01:39', 'C*01:40', 'C*02:02', 'C*02:03', 'C*02:04', 'C*02:05', 'C*02:06',
                 'C*02:07', 'C*02:08', 'C*02:09', 'C*02:10', 'C*02:11', 'C*02:12', 'C*02:13', 'C*02:14', 'C*02:15',
                 'C*02:16', 'C*02:17', 'C*02:18', 'C*02:19', 'C*02:20', 'C*02:21', 'C*02:22', 'C*02:23', 'C*02:24',
                 'C*02:26', 'C*02:27', 'C*02:28', 'C*02:29', 'C*02:30', 'C*02:31', 'C*02:32', 'C*02:33', 'C*02:34',
                 'C*02:35', 'C*02:36', 'C*02:37', 'C*02:39', 'C*02:40', 'C*03:01', 'C*03:02', 'C*03:03', 'C*03:04',
                 'C*03:05', 'C*03:06', 'C*03:07', 'C*03:08', 'C*03:09', 'C*03:10', 'C*03:11', 'C*03:12', 'C*03:13',
                 'C*03:14', 'C*03:15', 'C*03:16', 'C*03:17', 'C*03:18', 'C*03:19', 'C*03:21', 'C*03:23', 'C*03:24',
                 'C*03:25', 'C*03:26', 'C*03:27', 'C*03:28', 'C*03:29', 'C*03:30', 'C*03:31', 'C*03:32', 'C*03:33',
                 'C*03:34', 'C*03:35', 'C*03:36', 'C*03:37', 'C*03:38', 'C*03:39', 'C*03:40', 'C*03:41', 'C*03:42',
                 'C*03:43', 'C*03:44', 'C*03:45', 'C*03:46', 'C*03:47', 'C*03:48', 'C*03:49', 'C*03:50', 'C*03:51',
                 'C*03:52', 'C*03:53', 'C*03:54', 'C*03:55', 'C*03:56', 'C*03:57', 'C*03:58', 'C*03:59', 'C*03:60',
                 'C*03:61', 'C*03:62', 'C*03:63', 'C*03:64', 'C*03:65', 'C*03:66', 'C*03:67', 'C*03:68', 'C*03:69',
                 'C*03:70', 'C*03:71', 'C*03:72', 'C*03:73', 'C*03:74', 'C*03:75', 'C*03:76', 'C*03:77', 'C*03:78',
                 'C*03:79', 'C*03:80', 'C*03:81', 'C*03:82', 'C*03:83', 'C*03:84', 'C*03:85', 'C*03:86', 'C*03:87',
                 'C*03:88', 'C*03:89', 'C*03:90', 'C*03:91', 'C*03:92', 'C*03:93', 'C*03:94', 'C*04:01', 'C*04:03',
                 'C*04:04', 'C*04:05', 'C*04:06', 'C*04:07', 'C*04:08', 'C*04:10', 'C*04:11', 'C*04:12', 'C*04:13',
                 'C*04:14', 'C*04:15', 'C*04:16', 'C*04:17', 'C*04:18', 'C*04:19', 'C*04:20', 'C*04:23', 'C*04:24',
                 'C*04:25', 'C*04:26', 'C*04:27', 'C*04:28', 'C*04:29', 'C*04:30', 'C*04:31', 'C*04:32', 'C*04:33',
                 'C*04:34', 'C*04:35', 'C*04:36', 'C*04:37', 'C*04:38', 'C*04:39', 'C*04:40', 'C*04:41', 'C*04:42',
                 'C*04:43', 'C*04:44', 'C*04:45', 'C*04:46', 'C*04:47', 'C*04:48', 'C*04:49', 'C*04:50', 'C*04:51',
                 'C*04:52', 'C*04:53', 'C*04:54', 'C*04:55', 'C*04:56', 'C*04:57', 'C*04:58', 'C*04:60', 'C*04:61',
                 'C*04:62', 'C*04:63', 'C*04:64', 'C*04:65', 'C*04:66', 'C*04:67', 'C*04:68', 'C*04:69', 'C*04:70',
                 'C*05:01', 'C*05:03', 'C*05:04', 'C*05:05', 'C*05:06', 'C*05:08', 'C*05:09', 'C*05:10', 'C*05:11',
                 'C*05:12', 'C*05:13', 'C*05:14', 'C*05:15', 'C*05:16', 'C*05:17', 'C*05:18', 'C*05:19', 'C*05:20',
                 'C*05:21', 'C*05:22', 'C*05:23', 'C*05:24', 'C*05:25', 'C*05:26', 'C*05:27', 'C*05:28', 'C*05:29',
                 'C*05:30', 'C*05:31', 'C*05:32', 'C*05:33', 'C*05:34', 'C*05:35', 'C*05:36', 'C*05:37', 'C*05:38',
                 'C*05:39', 'C*05:40', 'C*05:41', 'C*05:42', 'C*05:43', 'C*05:44', 'C*05:45', 'C*06:02', 'C*06:03',
                 'C*06:04', 'C*06:05', 'C*06:06', 'C*06:07', 'C*06:08', 'C*06:09', 'C*06:10', 'C*06:11', 'C*06:12',
                 'C*06:13', 'C*06:14', 'C*06:15', 'C*06:17', 'C*06:18', 'C*06:19', 'C*06:20', 'C*06:21', 'C*06:22',
                 'C*06:23', 'C*06:24', 'C*06:25', 'C*06:26', 'C*06:27', 'C*06:28', 'C*06:29', 'C*06:30', 'C*06:31',
                 'C*06:32', 'C*06:33', 'C*06:34', 'C*06:35', 'C*06:36', 'C*06:37', 'C*06:38', 'C*06:39', 'C*06:40',
                 'C*06:41', 'C*06:42', 'C*06:43', 'C*06:44', 'C*06:45', 'C*07:01', 'C*07:02', 'C*07:03', 'C*07:04',
                 'C*07:05', 'C*07:06', 'C*07:07', 'C*07:08', 'C*07:09', 'C*07:10', 'C*07:100', 'C*07:101', 'C*07:102',
                 'C*07:103', 'C*07:105', 'C*07:106', 'C*07:107', 'C*07:108', 'C*07:109', 'C*07:11', 'C*07:110',
                 'C*07:111', 'C*07:112', 'C*07:113', 'C*07:114', 'C*07:115', 'C*07:116', 'C*07:117', 'C*07:118',
                 'C*07:119', 'C*07:12', 'C*07:120', 'C*07:122', 'C*07:123', 'C*07:124', 'C*07:125', 'C*07:126',
                 'C*07:127', 'C*07:128', 'C*07:129', 'C*07:13', 'C*07:130', 'C*07:131', 'C*07:132', 'C*07:133',
                 'C*07:134', 'C*07:135', 'C*07:136', 'C*07:137', 'C*07:138', 'C*07:139', 'C*07:14', 'C*07:140',
                 'C*07:141', 'C*07:142', 'C*07:143', 'C*07:144', 'C*07:145', 'C*07:146', 'C*07:147', 'C*07:148',
                 'C*07:149', 'C*07:15', 'C*07:16', 'C*07:17', 'C*07:18', 'C*07:19', 'C*07:20', 'C*07:21', 'C*07:22',
                 'C*07:23', 'C*07:24', 'C*07:25', 'C*07:26', 'C*07:27', 'C*07:28', 'C*07:29', 'C*07:30', 'C*07:31',
                 'C*07:35', 'C*07:36', 'C*07:37', 'C*07:38', 'C*07:39', 'C*07:40', 'C*07:41', 'C*07:42', 'C*07:43',
                 'C*07:44', 'C*07:45', 'C*07:46', 'C*07:47', 'C*07:48', 'C*07:49', 'C*07:50', 'C*07:51', 'C*07:52',
                 'C*07:53', 'C*07:54', 'C*07:56', 'C*07:57', 'C*07:58', 'C*07:59', 'C*07:60', 'C*07:62', 'C*07:63',
                 'C*07:64', 'C*07:65', 'C*07:66', 'C*07:67', 'C*07:68', 'C*07:69', 'C*07:70', 'C*07:71', 'C*07:72',
                 'C*07:73', 'C*07:74', 'C*07:75', 'C*07:76', 'C*07:77', 'C*07:78', 'C*07:79', 'C*07:80', 'C*07:81',
                 'C*07:82', 'C*07:83', 'C*07:84', 'C*07:85', 'C*07:86', 'C*07:87', 'C*07:88', 'C*07:89', 'C*07:90',
                 'C*07:91', 'C*07:92', 'C*07:93', 'C*07:94', 'C*07:95', 'C*07:96', 'C*07:97', 'C*07:99', 'C*08:01',
                 'C*08:02', 'C*08:03', 'C*08:04', 'C*08:05', 'C*08:06', 'C*08:07', 'C*08:08', 'C*08:09', 'C*08:10',
                 'C*08:11', 'C*08:12', 'C*08:13', 'C*08:14', 'C*08:15', 'C*08:16', 'C*08:17', 'C*08:18', 'C*08:19',
                 'C*08:20', 'C*08:21', 'C*08:22', 'C*08:23', 'C*08:24', 'C*08:25', 'C*08:27', 'C*08:28', 'C*08:29',
                 'C*08:30', 'C*08:31', 'C*08:32', 'C*08:33', 'C*08:34', 'C*08:35', 'C*12:02', 'C*12:03', 'C*12:04',
                 'C*12:05', 'C*12:06', 'C*12:07', 'C*12:08', 'C*12:09', 'C*12:10', 'C*12:11', 'C*12:12', 'C*12:13',
                 'C*12:14', 'C*12:15', 'C*12:16', 'C*12:17', 'C*12:18', 'C*12:19', 'C*12:20', 'C*12:21', 'C*12:22',
                 'C*12:23', 'C*12:24', 'C*12:25', 'C*12:26', 'C*12:27', 'C*12:28', 'C*12:29', 'C*12:30', 'C*12:31',
                 'C*12:32', 'C*12:33', 'C*12:34', 'C*12:35', 'C*12:36', 'C*12:37', 'C*12:38', 'C*12:40', 'C*12:41',
                 'C*12:43', 'C*12:44', 'C*14:02', 'C*14:03', 'C*14:04', 'C*14:05', 'C*14:06', 'C*14:08', 'C*14:09',
                 'C*14:10', 'C*14:11', 'C*14:12', 'C*14:13', 'C*14:14', 'C*14:15', 'C*14:16', 'C*14:17', 'C*14:18',
                 'C*14:19', 'C*14:20', 'C*15:02', 'C*15:03', 'C*15:04', 'C*15:05', 'C*15:06', 'C*15:07', 'C*15:08',
                 'C*15:09', 'C*15:10', 'C*15:11', 'C*15:12', 'C*15:13', 'C*15:15', 'C*15:16', 'C*15:17', 'C*15:18',
                 'C*15:19', 'C*15:20', 'C*15:21', 'C*15:22', 'C*15:23', 'C*15:24', 'C*15:25', 'C*15:26', 'C*15:27',
                 'C*15:28', 'C*15:29', 'C*15:30', 'C*15:31', 'C*15:33', 'C*15:34', 'C*15:35', 'C*16:01', 'C*16:02',
                 'C*16:04', 'C*16:06', 'C*16:07', 'C*16:08', 'C*16:09', 'C*16:10', 'C*16:11', 'C*16:12', 'C*16:13',
                 'C*16:14', 'C*16:15', 'C*16:17', 'C*16:18', 'C*16:19', 'C*16:20', 'C*16:21', 'C*16:22', 'C*16:23',
                 'C*16:24', 'C*16:25', 'C*16:26', 'C*17:01', 'C*17:02', 'C*17:03', 'C*17:04', 'C*17:05', 'C*17:06',
                 'C*17:07', 'C*18:01', 'C*18:02', 'C*18:03', 'E*01:01', 'G*01:01', 'G*01:02', 'G*01:03', 'G*01:04',
                 'G*01:06', 'G*01:07', 'G*01:08', 'G*01:09'])

    @property
    def version(self):
        return self.__version

    def convert_alleles(self, alleles):
        return ["HLA-%s%s:%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    def get_external_version(self, path=None):
        #can not be determined netmhcpan does not support --version or similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(_input))

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        f = csv.reader(open(_file, "r"), delimiter='\t')
        alleles = set(filter(lambda x: x != "", f.next()))
        f.next()
        ic_pos = 3
        for row in f:
            pep_seq = row[1]
            for i, a in enumerate(alleles):
                result[a][pep_seq] = float(row[ic_pos+i*3])
        return result


class NetMHCII_2_2(AExternalEpitopePrediction):
    """
        Implements a wrapper for NetMHCII

        Nielsen, M., & Lund, O. (2009). NN-align. An artificial neural network-based alignment algorithm for MHC class
        II peptide binding prediction. BMC Bioinformatics, 10(1), 296.

        Nielsen, M., Lundegaard, C., & Lund, O. (2007). Prediction of MHC class II binding affinity using SMM-align,
        a novel stabilization matrix alignment method. BMC Bioinformatics, 8(1), 238.
    """
    __supported_length = frozenset([15])
    __name = "netmhcII"
    __command = 'netMHCII {peptides} -a {alleles} {options} | grep -v "#" > {out}'
    __alleles = frozenset(
        ['DRB1*01:01', 'DRB1*03:01', 'DRB1*04:01', 'DRB1*04:04', 'DRB1*04:05', 'DRB1*07:01', 'DRB1*08:02', 'DRB1*09:01',
         'DRB1*11:01', 'DRB1*13:02', 'DRB1*15:01', 'DRB3*01:01', 'DRB4*01:01', 'DRB5*01:01'])
    __version = "2.2"

    @property
    def version(self):
        return self.__version

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    def convert_alleles(self, alleles):
        return ["HLA-%s%s%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        f = csv.reader(open(_file, "r"), delimiter='\t')
        for r in f:
            if not r:
                continue

            row = r[0].split()
            if not len(row):
                continue

            if "HLA-" not in row[0]:
                continue
            result[row[0]][row[2]] = float(row[4])
        return result

    def get_external_version(self, path=None):
        #can not be determined netmhcpan does not support --version or similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(">pepe_%i\n%s"%(i, p) for i, p in enumerate(_input)))


class NetMHCIIpan_3_0(AExternalEpitopePrediction):
    """
        Implements a wrapper for NetMHCIIpan

        Andreatta, M., Karosiene, E., Rasmussen, M., Stryhn, A., Buus, S., & Nielsen, M. (2015).
        Accurate pan-specific prediction of peptide-MHC class II binding affinity with improved binding
        core identification. Immunogenetics, 1-10.
    """

    __supported_length = frozenset([8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    __name = "netmchIIpan"
    __command = "netMHCIIpan -f {peptides} -inptype 1 -a {alleles} {options} -xls -xlsfile {out}"
    __alleles = frozenset(['DRB1*01:01', 'DRB1*01:02', 'DRB1*01:03', 'DRB1*01:04', 'DRB1*01:05', 'DRB1*01:06', 'DRB1*01:07',
                 'DRB1*01:08', 'DRB1*01:09', 'DRB1*01:10', 'DRB1*01:11', 'DRB1*01:12', 'DRB1*01:13', 'DRB1*01:14',
                 'DRB1*01:15', 'DRB1*01:16', 'DRB1*01:17', 'DRB1*01:18', 'DRB1*01:19', 'DRB1*01:20', 'DRB1*01:21',
                 'DRB1*01:22', 'DRB1*01:23', 'DRB1*01:24', 'DRB1*01:25', 'DRB1*01:26', 'DRB1*01:27', 'DRB1*01:28',
                 'DRB1*01:29', 'DRB1*01:30', 'DRB1*01:31', 'DRB1*01:32', 'DRB1*03:01', 'DRB1*03:02', 'DRB1*03:03',
                 'DRB1*03:04', 'DRB1*03:05', 'DRB1*03:06', 'DRB1*03:07', 'DRB1*03:08', 'DRB1*03:10', 'DRB1*03:11',
                 'DRB1*03:13', 'DRB1*03:14', 'DRB1*03:15', 'DRB1*03:17', 'DRB1*03:18', 'DRB1*03:19', 'DRB1*03:20',
                 'DRB1*03:21', 'DRB1*03:22', 'DRB1*03:23', 'DRB1*03:24', 'DRB1*03:25', 'DRB1*03:26', 'DRB1*03:27',
                 'DRB1*03:28', 'DRB1*03:29', 'DRB1*03:30', 'DRB1*03:31', 'DRB1*03:32', 'DRB1*03:33', 'DRB1*03:34',
                 'DRB1*03:35', 'DRB1*03:36', 'DRB1*03:37', 'DRB1*03:38', 'DRB1*03:39', 'DRB1*03:40', 'DRB1*03:41',
                 'DRB1*03:42', 'DRB1*03:43', 'DRB1*03:44', 'DRB1*03:45', 'DRB1*03:46', 'DRB1*03:47', 'DRB1*03:48',
                 'DRB1*03:49', 'DRB1*03:50', 'DRB1*03:51', 'DRB1*03:52', 'DRB1*03:53', 'DRB1*03:54', 'DRB1*03:55',
                 'DRB1*04:01', 'DRB1*04:02', 'DRB1*04:03', 'DRB1*04:04', 'DRB1*04:05', 'DRB1*04:06', 'DRB1*04:07',
                 'DRB1*04:08', 'DRB1*04:09', 'DRB1*04:10', 'DRB1*04:11', 'DRB1*04:12', 'DRB1*04:13', 'DRB1*04:14',
                 'DRB1*04:15', 'DRB1*04:16', 'DRB1*04:17', 'DRB1*04:18', 'DRB1*04:19', 'DRB1*04:21', 'DRB1*04:22',
                 'DRB1*04:23', 'DRB1*04:24', 'DRB1*04:26', 'DRB1*04:27', 'DRB1*04:28', 'DRB1*04:29', 'DRB1*04:30',
                 'DRB1*04:31', 'DRB1*04:33', 'DRB1*04:34', 'DRB1*04:35', 'DRB1*04:36', 'DRB1*04:37', 'DRB1*04:38',
                 'DRB1*04:39', 'DRB1*04:40', 'DRB1*04:41', 'DRB1*04:42', 'DRB1*04:43', 'DRB1*04:44', 'DRB1*04:45',
                 'DRB1*04:46', 'DRB1*04:47', 'DRB1*04:48', 'DRB1*04:49', 'DRB1*04:50', 'DRB1*04:51', 'DRB1*04:52',
                 'DRB1*04:53', 'DRB1*04:54', 'DRB1*04:55', 'DRB1*04:56', 'DRB1*04:57', 'DRB1*04:58', 'DRB1*04:59',
                 'DRB1*04:60', 'DRB1*04:61', 'DRB1*04:62', 'DRB1*04:63', 'DRB1*04:64', 'DRB1*04:65', 'DRB1*04:66',
                 'DRB1*04:67', 'DRB1*04:68', 'DRB1*04:69', 'DRB1*04:70', 'DRB1*04:71', 'DRB1*04:72', 'DRB1*04:73',
                 'DRB1*04:74', 'DRB1*04:75', 'DRB1*04:76', 'DRB1*04:77', 'DRB1*04:78', 'DRB1*04:79', 'DRB1*04:80',
                 'DRB1*04:82', 'DRB1*04:83', 'DRB1*04:84', 'DRB1*04:85', 'DRB1*04:86', 'DRB1*04:87', 'DRB1*04:88',
                 'DRB1*04:89', 'DRB1*04:91', 'DRB1*07:01', 'DRB1*07:03', 'DRB1*07:04', 'DRB1*07:05', 'DRB1*07:06',
                 'DRB1*07:07', 'DRB1*07:08', 'DRB1*07:09', 'DRB1*07:11', 'DRB1*07:12', 'DRB1*07:13', 'DRB1*07:14',
                 'DRB1*07:15', 'DRB1*07:16', 'DRB1*07:17', 'DRB1*07:19', 'DRB1*08:01', 'DRB1*08:02', 'DRB1*08:03',
                 'DRB1*08:04', 'DRB1*08:05', 'DRB1*08:06', 'DRB1*08:07', 'DRB1*08:08', 'DRB1*08:09', 'DRB1*08:10',
                 'DRB1*08:11', 'DRB1*08:12', 'DRB1*08:13', 'DRB1*08:14', 'DRB1*08:15', 'DRB1*08:16', 'DRB1*08:18',
                 'DRB1*08:19', 'DRB1*08:20', 'DRB1*08:21', 'DRB1*08:22', 'DRB1*08:23', 'DRB1*08:24', 'DRB1*08:25',
                 'DRB1*08:26', 'DRB1*08:27', 'DRB1*08:28', 'DRB1*08:29', 'DRB1*08:30', 'DRB1*08:31', 'DRB1*08:32',
                 'DRB1*08:33', 'DRB1*08:34', 'DRB1*08:35', 'DRB1*08:36', 'DRB1*08:37', 'DRB1*08:38', 'DRB1*08:39',
                 'DRB1*08:40', 'DRB1*09:01', 'DRB1*09:02', 'DRB1*09:03', 'DRB1*09:04', 'DRB1*09:05', 'DRB1*09:06',
                 'DRB1*09:07', 'DRB1*09:08', 'DRB1*09:09', 'DRB1*10:01', 'DRB1*10:02', 'DRB1*10:03', 'DRB1*11:01',
                 'DRB1*11:02', 'DRB1*11:03', 'DRB1*11:04', 'DRB1*11:05', 'DRB1*11:06', 'DRB1*11:07', 'DRB1*11:08',
                 'DRB1*11:09', 'DRB1*11:10', 'DRB1*11:11', 'DRB1*11:12', 'DRB1*11:13', 'DRB1*11:14', 'DRB1*11:15',
                 'DRB1*11:16', 'DRB1*11:17', 'DRB1*11:18', 'DRB1*11:19', 'DRB1*11:20', 'DRB1*11:21', 'DRB1*11:24',
                 'DRB1*11:25', 'DRB1*11:27', 'DRB1*11:28', 'DRB1*11:29', 'DRB1*11:30', 'DRB1*11:31', 'DRB1*11:32',
                 'DRB1*11:33', 'DRB1*11:34', 'DRB1*11:35', 'DRB1*11:36', 'DRB1*11:37', 'DRB1*11:38', 'DRB1*11:39',
                 'DRB1*11:41', 'DRB1*11:42', 'DRB1*11:43', 'DRB1*11:44', 'DRB1*11:45', 'DRB1*11:46', 'DRB1*11:47',
                 'DRB1*11:48', 'DRB1*11:49', 'DRB1*11:50', 'DRB1*11:51', 'DRB1*11:52', 'DRB1*11:53', 'DRB1*11:54',
                 'DRB1*11:55', 'DRB1*11:56', 'DRB1*11:57', 'DRB1*11:58', 'DRB1*11:59', 'DRB1*11:60', 'DRB1*11:61',
                 'DRB1*11:62', 'DRB1*11:63', 'DRB1*11:64', 'DRB1*11:65', 'DRB1*11:66', 'DRB1*11:67', 'DRB1*11:68',
                 'DRB1*11:69', 'DRB1*11:70', 'DRB1*11:72', 'DRB1*11:73', 'DRB1*11:74', 'DRB1*11:75', 'DRB1*11:76',
                 'DRB1*11:77', 'DRB1*11:78', 'DRB1*11:79', 'DRB1*11:80', 'DRB1*11:81', 'DRB1*11:82', 'DRB1*11:83',
                 'DRB1*11:84', 'DRB1*11:85', 'DRB1*11:86', 'DRB1*11:87', 'DRB1*11:88', 'DRB1*11:89', 'DRB1*11:90',
                 'DRB1*11:91', 'DRB1*11:92', 'DRB1*11:93', 'DRB1*11:94', 'DRB1*11:95', 'DRB1*11:96', 'DRB1*12:01',
                 'DRB1*12:02', 'DRB1*12:03', 'DRB1*12:04', 'DRB1*12:05', 'DRB1*12:06', 'DRB1*12:07', 'DRB1*12:08',
                 'DRB1*12:09', 'DRB1*12:10', 'DRB1*12:11', 'DRB1*12:12', 'DRB1*12:13', 'DRB1*12:14', 'DRB1*12:15',
                 'DRB1*12:16', 'DRB1*12:17', 'DRB1*12:18', 'DRB1*12:19', 'DRB1*12:20', 'DRB1*12:21', 'DRB1*12:22',
                 'DRB1*12:23', 'DRB1*13:01', 'DRB1*13:02', 'DRB1*13:03', 'DRB1*13:04', 'DRB1*13:05', 'DRB1*13:06',
                 'DRB1*13:07', 'DRB1*13:08', 'DRB1*13:09', 'DRB1*13:10', 'DRB1*13:10:0', 'DRB1*13:10:1', 'DRB1*13:11',
                 'DRB1*13:12', 'DRB1*13:13', 'DRB1*13:14', 'DRB1*13:15', 'DRB1*13:16', 'DRB1*13:17', 'DRB1*13:18',
                 'DRB1*13:19', 'DRB1*13:20', 'DRB1*13:21', 'DRB1*13:22', 'DRB1*13:23', 'DRB1*13:24', 'DRB1*13:26',
                 'DRB1*13:27', 'DRB1*13:29', 'DRB1*13:30', 'DRB1*13:31', 'DRB1*13:32', 'DRB1*13:33', 'DRB1*13:34',
                 'DRB1*13:35', 'DRB1*13:36', 'DRB1*13:37', 'DRB1*13:38', 'DRB1*13:39', 'DRB1*13:41', 'DRB1*13:42',
                 'DRB1*13:43', 'DRB1*13:44', 'DRB1*13:46', 'DRB1*13:47', 'DRB1*13:48', 'DRB1*13:49', 'DRB1*13:50',
                 'DRB1*13:51', 'DRB1*13:52', 'DRB1*13:53', 'DRB1*13:54', 'DRB1*13:55', 'DRB1*13:56', 'DRB1*13:57',
                 'DRB1*13:58', 'DRB1*13:59', 'DRB1*13:60', 'DRB1*13:61', 'DRB1*13:62', 'DRB1*13:63', 'DRB1*13:64',
                 'DRB1*13:65', 'DRB1*13:66', 'DRB1*13:67', 'DRB1*13:68', 'DRB1*13:69', 'DRB1*13:70', 'DRB1*13:71',
                 'DRB1*13:72', 'DRB1*13:73', 'DRB1*13:74', 'DRB1*13:75', 'DRB1*13:76', 'DRB1*13:77', 'DRB1*13:78',
                 'DRB1*13:79', 'DRB1*13:80', 'DRB1*13:81', 'DRB1*13:82', 'DRB1*13:83', 'DRB1*13:84', 'DRB1*13:85',
                 'DRB1*13:86', 'DRB1*13:87', 'DRB1*13:88', 'DRB1*13:89', 'DRB1*13:90', 'DRB1*13:91', 'DRB1*13:92',
                 'DRB1*13:93', 'DRB1*13:94', 'DRB1*13:95', 'DRB1*13:96', 'DRB1*13:97', 'DRB1*13:98', 'DRB1*13:99',
                 'DRB1*14:01', 'DRB1*14:02', 'DRB1*14:03', 'DRB1*14:04', 'DRB1*14:05', 'DRB1*14:06', 'DRB1*14:07',
                 'DRB1*14:08', 'DRB1*14:09', 'DRB1*14:10', 'DRB1*14:11', 'DRB1*14:12', 'DRB1*14:13', 'DRB1*14:14',
                 'DRB1*14:15', 'DRB1*14:16', 'DRB1*14:17', 'DRB1*14:18', 'DRB1*14:19', 'DRB1*14:20', 'DRB1*14:21',
                 'DRB1*14:22', 'DRB1*14:23', 'DRB1*14:24', 'DRB1*14:25', 'DRB1*14:26', 'DRB1*14:27', 'DRB1*14:28',
                 'DRB1*14:29', 'DRB1*14:30', 'DRB1*14:31', 'DRB1*14:32', 'DRB1*14:33', 'DRB1*14:34', 'DRB1*14:35',
                 'DRB1*14:36', 'DRB1*14:37', 'DRB1*14:38', 'DRB1*14:39', 'DRB1*14:40', 'DRB1*14:41', 'DRB1*14:42',
                 'DRB1*14:43', 'DRB1*14:44', 'DRB1*14:45', 'DRB1*14:46', 'DRB1*14:47', 'DRB1*14:48', 'DRB1*14:49',
                 'DRB1*14:50', 'DRB1*14:51', 'DRB1*14:52', 'DRB1*14:53', 'DRB1*14:54', 'DRB1*14:55', 'DRB1*14:56',
                 'DRB1*14:57', 'DRB1*14:58', 'DRB1*14:59', 'DRB1*14:60', 'DRB1*14:61', 'DRB1*14:62', 'DRB1*14:63',
                 'DRB1*14:64', 'DRB1*14:65', 'DRB1*14:67', 'DRB1*14:68', 'DRB1*14:69', 'DRB1*14:70', 'DRB1*14:71',
                 'DRB1*14:72', 'DRB1*14:73', 'DRB1*14:74', 'DRB1*14:75', 'DRB1*14:76', 'DRB1*14:77', 'DRB1*14:78',
                 'DRB1*14:79', 'DRB1*14:80', 'DRB1*14:81', 'DRB1*14:82', 'DRB1*14:83', 'DRB1*14:84', 'DRB1*14:85',
                 'DRB1*14:86', 'DRB1*14:87', 'DRB1*14:88', 'DRB1*14:89', 'DRB1*14:90', 'DRB1*14:91', 'DRB1*14:93',
                 'DRB1*14:94', 'DRB1*14:95', 'DRB1*14:96', 'DRB1*14:97', 'DRB1*14:98', 'DRB1*14:99', 'DRB1*15:01',
                 'DRB1*15:02', 'DRB1*15:03', 'DRB1*15:04', 'DRB1*15:05', 'DRB1*15:06', 'DRB1*15:07', 'DRB1*15:08',
                 'DRB1*15:09', 'DRB1*15:10', 'DRB1*15:11', 'DRB1*15:12', 'DRB1*15:13', 'DRB1*15:14', 'DRB1*15:15',
                 'DRB1*15:16', 'DRB1*15:18', 'DRB1*15:19', 'DRB1*15:20', 'DRB1*15:21', 'DRB1*15:22', 'DRB1*15:23',
                 'DRB1*15:24', 'DRB1*15:25', 'DRB1*15:26', 'DRB1*15:27', 'DRB1*15:28', 'DRB1*15:29', 'DRB1*15:30',
                 'DRB1*15:31', 'DRB1*15:32', 'DRB1*15:33', 'DRB1*15:34', 'DRB1*15:35', 'DRB1*15:36', 'DRB1*15:37',
                 'DRB1*15:38', 'DRB1*15:39', 'DRB1*15:40', 'DRB1*15:41', 'DRB1*15:42', 'DRB1*15:43', 'DRB1*15:44',
                 'DRB1*15:45', 'DRB1*15:46', 'DRB1*15:47', 'DRB1*15:48', 'DRB1*15:49', 'DRB1*16:01', 'DRB1*16:02',
                 'DRB1*16:03', 'DRB1*16:04', 'DRB1*16:05', 'DRB1*16:07', 'DRB1*16:08', 'DRB1*16:09', 'DRB1*16:10',
                 'DRB1*16:11', 'DRB1*16:12', 'DRB1*16:14', 'DRB1*16:15', 'DRB1*16:16', 'DRB3*01:01', 'DRB3*01:04',
                 'DRB3*01:05', 'DRB3*01:08', 'DRB3*01:09', 'DRB3*01:11', 'DRB3*01:12', 'DRB3*01:13', 'DRB3*01:14',
                 'DRB3*02:01', 'DRB3*02:02', 'DRB3*02:04', 'DRB3*02:05', 'DRB3*02:09', 'DRB3*02:10', 'DRB3*02:11',
                 'DRB3*02:12', 'DRB3*02:13', 'DRB3*02:14', 'DRB3*02:15', 'DRB3*02:16', 'DRB3*02:17', 'DRB3*02:18',
                 'DRB3*02:19', 'DRB3*02:20', 'DRB3*02:21', 'DRB3*02:22', 'DRB3*02:23', 'DRB3*02:24', 'DRB3*02:25',
                 'DRB3*03:01', 'DRB3*03:03', 'DRB4*01:01', 'DRB4*01:03', 'DRB4*01:04', 'DRB4*01:06', 'DRB4*01:07',
                 'DRB4*01:08', 'DRB5*01:01', 'DRB5*01:02', 'DRB5*01:03', 'DRB5*01:04', 'DRB5*01:05', 'DRB5*01:06',
                 'DRB5*01:08N', 'DRB5*01:11', 'DRB5*01:12', 'DRB5*01:13', 'DRB5*01:14', 'DRB5*02:02', 'DRB5*02:03',
                 'DRB5*02:04', 'DRB5*02:05'])
    __version = "3.0"

    @property
    def version(self):
        return self.__version

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    def convert_alleles(self, alleles):
        return ["%s_%s%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        f = csv.reader(open(_file, "r"), delimiter='\t')
        alleles = map(lambda x: x.replace("*", "_").replace(":", ""), set(filter(lambda x: x != "", f.next())))
        f.next()
        ic_pos = 3
        for row in f:
            pep_seq = row[1]
            for i, a in enumerate(alleles):
                result[a][pep_seq] = float(row[ic_pos + i * 3])
        return result

    def get_external_version(self, path=None):
        #can't be determined method does not support --version or similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(_input))


class PickPocket_1_1(AExternalEpitopePrediction):
    """
    Implementation of PickPocket adapter

    Zhang, H., Lund, O., & Nielsen, M. (2009). The PickPocket method for predicting binding specificities
    for receptors based on receptor pocket similarities: application to MHC-peptide binding.
    Bioinformatics, 25(10), 1293-1299.

    """
    __name = "pickpocket"
    __supported_length = frozenset([8, 9, 10, 11])
    __command = 'PickPocket -p {peptides} -a {alleles} {options} | grep -v "#" > {out}'
    __supported_alleles = frozenset(['A*01:01', 'A*01:02', 'A*01:03', 'A*01:06', 'A*01:07', 'A*01:08', 'A*01:09',
        'A*01:10', 'A*01:12', 'A*01:13', 'A*01:14', 'A*01:17', 'A*01:19', 'A*01:20', 'A*01:21', 'A*01:23', 'A*01:24',
        'A*01:25', 'A*01:26', 'A*01:28', 'A*01:29', 'A*01:30', 'A*01:32', 'A*01:33', 'A*01:35', 'A*01:36', 'A*01:37',
        'A*01:38', 'A*01:39', 'A*01:40', 'A*01:41', 'A*01:42', 'A*01:43', 'A*01:44', 'A*01:45', 'A*01:46', 'A*01:47',
        'A*01:48', 'A*01:49', 'A*01:50', 'A*01:51', 'A*01:54', 'A*01:55', 'A*01:58', 'A*01:59', 'A*01:60', 'A*01:61',
        'A*01:62', 'A*01:63', 'A*01:64', 'A*01:65', 'A*01:66', 'A*02:01', 'A*02:02', 'A*02:03', 'A*02:04', 'A*02:05',
        'A*02:06', 'A*02:07', 'A*02:08', 'A*02:09', 'A*02:10', 'A*02:11', 'A*02:12', 'A*02:13', 'A*02:14', 'A*02:16',
        'A*02:17', 'A*02:18', 'A*02:19', 'A*02:20', 'A*02:21', 'A*02:22', 'A*02:24', 'A*02:25', 'A*02:26', 'A*02:27',
        'A*02:28', 'A*02:29', 'A*02:30', 'A*02:31', 'A*02:33', 'A*02:34', 'A*02:35', 'A*02:36', 'A*02:37', 'A*02:38',
        'A*02:39', 'A*02:40', 'A*02:41', 'A*02:42', 'A*02:44', 'A*02:45', 'A*02:46', 'A*02:47', 'A*02:48', 'A*02:49',
        'A*02:50', 'A*02:51', 'A*02:52', 'A*02:54', 'A*02:55', 'A*02:56', 'A*02:57', 'A*02:58', 'A*02:59', 'A*02:60',
        'A*02:61', 'A*02:62', 'A*02:63', 'A*02:64', 'A*02:65', 'A*02:66', 'A*02:67', 'A*02:68', 'A*02:69', 'A*02:70',
        'A*02:71', 'A*02:72', 'A*02:73', 'A*02:74', 'A*02:75', 'A*02:76', 'A*02:77', 'A*02:78', 'A*02:79', 'A*02:80',
        'A*02:81', 'A*02:84', 'A*02:85', 'A*02:86', 'A*02:87', 'A*02:89', 'A*02:90', 'A*02:91', 'A*02:92', 'A*02:93',
        'A*02:95', 'A*02:96', 'A*02:97', 'A*02:99', 'A*02:101', 'A*02:102', 'A*02:103', 'A*02:104', 'A*02:105',
        'A*02:106', 'A*02:107', 'A*02:108', 'A*02:109', 'A*02:110', 'A*02:111', 'A*02:112', 'A*02:114', 'A*02:115',
        'A*02:116', 'A*02:117', 'A*02:118', 'A*02:119', 'A*02:120', 'A*02:121', 'A*02:122', 'A*02:123', 'A*02:124',
        'A*02:126', 'A*02:127', 'A*02:128', 'A*02:129', 'A*02:130', 'A*02:131', 'A*02:132', 'A*02:133', 'A*02:134',
        'A*02:135', 'A*02:136', 'A*02:137', 'A*02:138', 'A*02:139', 'A*02:140', 'A*02:141', 'A*02:142', 'A*02:143',
        'A*02:144', 'A*02:145', 'A*02:146', 'A*02:147', 'A*02:148', 'A*02:149', 'A*02:150', 'A*02:151', 'A*02:152',
        'A*02:153', 'A*02:154', 'A*02:155', 'A*02:156', 'A*02:157', 'A*02:158', 'A*02:159', 'A*02:160', 'A*02:161',
        'A*02:162', 'A*02:163', 'A*02:164', 'A*02:165', 'A*02:166', 'A*02:167', 'A*02:168', 'A*02:169', 'A*02:170',
        'A*02:171', 'A*02:172', 'A*02:173', 'A*02:174', 'A*02:175', 'A*02:176', 'A*02:177', 'A*02:178', 'A*02:179',
        'A*02:180', 'A*02:181', 'A*02:182', 'A*02:183', 'A*02:184', 'A*02:185', 'A*02:186', 'A*02:187', 'A*02:188',
        'A*02:189', 'A*02:190', 'A*02:191', 'A*02:192', 'A*02:193', 'A*02:194', 'A*02:195', 'A*02:196', 'A*02:197',
        'A*02:198', 'A*02:199', 'A*02:200', 'A*02:201', 'A*02:202', 'A*02:203', 'A*02:204', 'A*02:205', 'A*02:206',
        'A*02:207', 'A*02:208', 'A*02:209', 'A*02:210', 'A*02:211', 'A*02:212', 'A*02:213', 'A*02:214', 'A*02:215',
        'A*02:216', 'A*02:217', 'A*02:218', 'A*02:219', 'A*02:220', 'A*02:221', 'A*02:224', 'A*02:228', 'A*02:229',
        'A*02:230', 'A*02:231', 'A*02:232', 'A*02:233', 'A*02:234', 'A*02:235', 'A*02:236', 'A*02:237', 'A*02:238',
        'A*02:239', 'A*02:240', 'A*02:241', 'A*02:242', 'A*02:243', 'A*02:244', 'A*02:245', 'A*02:246', 'A*02:247',
        'A*02:248', 'A*02:249', 'A*02:251', 'A*02:252', 'A*02:253', 'A*02:254', 'A*02:255', 'A*02:256', 'A*02:257',
        'A*02:258', 'A*02:259', 'A*02:260', 'A*02:261', 'A*02:262', 'A*02:263', 'A*02:264', 'A*02:265', 'A*02:266',
        'A*03:01', 'A*03:02', 'A*03:04', 'A*03:05', 'A*03:06', 'A*03:07', 'A*03:08', 'A*03:09', 'A*03:10', 'A*03:12',
        'A*03:13', 'A*03:14', 'A*03:15', 'A*03:16', 'A*03:17', 'A*03:18', 'A*03:19', 'A*03:20', 'A*03:22', 'A*03:23',
        'A*03:24', 'A*03:25', 'A*03:26', 'A*03:27', 'A*03:28', 'A*03:29', 'A*03:30', 'A*03:31', 'A*03:32', 'A*03:33',
        'A*03:34', 'A*03:35', 'A*03:37', 'A*03:38', 'A*03:39', 'A*03:40', 'A*03:41', 'A*03:42', 'A*03:43', 'A*03:44',
        'A*03:45', 'A*03:46', 'A*03:47', 'A*03:48', 'A*03:49', 'A*03:50', 'A*03:51', 'A*03:52', 'A*03:53', 'A*03:54',
        'A*03:55', 'A*03:56', 'A*03:57', 'A*03:58', 'A*03:59', 'A*03:60', 'A*03:61', 'A*03:62', 'A*03:63', 'A*03:64',
        'A*03:65', 'A*03:66', 'A*03:67', 'A*03:70', 'A*03:71', 'A*03:72', 'A*03:73', 'A*03:74', 'A*03:75', 'A*03:76',
        'A*03:77', 'A*03:78', 'A*03:79', 'A*03:80', 'A*03:81', 'A*03:82', 'A*11:01', 'A*11:02', 'A*11:03', 'A*11:04',
        'A*11:05', 'A*11:06', 'A*11:07', 'A*11:08', 'A*11:09', 'A*11:10', 'A*11:11', 'A*11:12', 'A*11:13', 'A*11:14',
        'A*11:15', 'A*11:16', 'A*11:17', 'A*11:18', 'A*11:19', 'A*11:20', 'A*11:22', 'A*11:23', 'A*11:24', 'A*11:25',
        'A*11:26', 'A*11:27', 'A*11:29', 'A*11:30', 'A*11:31', 'A*11:32', 'A*11:33', 'A*11:34', 'A*11:35', 'A*11:36',
        'A*11:37', 'A*11:38', 'A*11:39', 'A*11:40', 'A*11:41', 'A*11:42', 'A*11:43', 'A*11:44', 'A*11:45', 'A*11:46',
        'A*11:47', 'A*11:48', 'A*11:49', 'A*11:51', 'A*11:53', 'A*11:54', 'A*11:55', 'A*11:56', 'A*11:57', 'A*11:58',
        'A*11:59', 'A*11:60', 'A*11:61', 'A*11:62', 'A*11:63', 'A*11:64', 'A*23:01', 'A*23:02', 'A*23:03', 'A*23:04',
        'A*23:05', 'A*23:06', 'A*23:09', 'A*23:10', 'A*23:12', 'A*23:13', 'A*23:14', 'A*23:15', 'A*23:16', 'A*23:17',
        'A*23:18', 'A*23:20', 'A*23:21', 'A*23:22', 'A*23:23', 'A*23:24', 'A*23:25', 'A*23:26', 'A*24:02', 'A*24:03',
        'A*24:04', 'A*24:05', 'A*24:06', 'A*24:07', 'A*24:08', 'A*24:10', 'A*24:13', 'A*24:14', 'A*24:15', 'A*24:17',
        'A*24:18', 'A*24:19', 'A*24:20', 'A*24:21', 'A*24:22', 'A*24:23', 'A*24:24', 'A*24:25', 'A*24:26', 'A*24:27',
        'A*24:28', 'A*24:29', 'A*24:30', 'A*24:31', 'A*24:32', 'A*24:33', 'A*24:34', 'A*24:35', 'A*24:37', 'A*24:38',
        'A*24:39', 'A*24:41', 'A*24:42', 'A*24:43', 'A*24:44', 'A*24:46', 'A*24:47', 'A*24:49', 'A*24:50', 'A*24:51',
        'A*24:52', 'A*24:53', 'A*24:54', 'A*24:55', 'A*24:56', 'A*24:57', 'A*24:58', 'A*24:59', 'A*24:61', 'A*24:62',
        'A*24:63', 'A*24:64', 'A*24:66', 'A*24:67', 'A*24:68', 'A*24:69', 'A*24:70', 'A*24:71', 'A*24:72', 'A*24:73',
        'A*24:74', 'A*24:75', 'A*24:76', 'A*24:77', 'A*24:78', 'A*24:79', 'A*24:80', 'A*24:81', 'A*24:82', 'A*24:85',
        'A*24:87', 'A*24:88', 'A*24:89', 'A*24:91', 'A*24:92', 'A*24:93', 'A*24:94', 'A*24:95', 'A*24:96', 'A*24:97',
        'A*24:98', 'A*24:99', 'A*24:100', 'A*24:101', 'A*24:102', 'A*24:103', 'A*24:104', 'A*24:105', 'A*24:106',
        'A*24:107', 'A*24:108', 'A*24:109', 'A*24:110', 'A*24:111', 'A*24:112', 'A*24:113', 'A*24:114', 'A*24:115',
        'A*24:116', 'A*24:117', 'A*24:118', 'A*24:119', 'A*24:120', 'A*24:121', 'A*24:122', 'A*24:123', 'A*24:124',
        'A*24:125', 'A*24:126', 'A*24:127', 'A*24:128', 'A*24:129', 'A*24:130', 'A*24:131', 'A*24:133', 'A*24:134',
        'A*24:135', 'A*24:136', 'A*24:137', 'A*24:138', 'A*24:139', 'A*24:140', 'A*24:141', 'A*24:142', 'A*24:143',
        'A*24:144', 'A*25:01', 'A*25:02', 'A*25:03', 'A*25:04', 'A*25:05', 'A*25:06', 'A*25:07', 'A*25:08', 'A*25:09',
        'A*25:10', 'A*25:11', 'A*25:13', 'A*26:01', 'A*26:02', 'A*26:03', 'A*26:04', 'A*26:05', 'A*26:06', 'A*26:07',
        'A*26:08', 'A*26:09', 'A*26:10', 'A*26:12', 'A*26:13', 'A*26:14', 'A*26:15', 'A*26:16', 'A*26:17', 'A*26:18',
        'A*26:19', 'A*26:20', 'A*26:21', 'A*26:22', 'A*26:23', 'A*26:24', 'A*26:26', 'A*26:27', 'A*26:28', 'A*26:29',
        'A*26:30', 'A*26:31', 'A*26:32', 'A*26:33', 'A*26:34', 'A*26:35', 'A*26:36', 'A*26:37', 'A*26:38', 'A*26:39',
        'A*26:40', 'A*26:41', 'A*26:42', 'A*26:43', 'A*26:45', 'A*26:46', 'A*26:47', 'A*26:48', 'A*26:49', 'A*26:50',
        'A*29:01', 'A*29:02', 'A*29:03', 'A*29:04', 'A*29:05', 'A*29:06', 'A*29:07', 'A*29:09', 'A*29:10', 'A*29:11',
        'A*29:12', 'A*29:13', 'A*29:14', 'A*29:15', 'A*29:16', 'A*29:17', 'A*29:18', 'A*29:19', 'A*29:20', 'A*29:21',
        'A*29:22', 'A*30:01', 'A*30:02', 'A*30:03', 'A*30:04', 'A*30:06', 'A*30:07', 'A*30:08', 'A*30:09', 'A*30:10',
        'A*30:11', 'A*30:12', 'A*30:13', 'A*30:15', 'A*30:16', 'A*30:17', 'A*30:18', 'A*30:19', 'A*30:20', 'A*30:22',
        'A*30:23', 'A*30:24', 'A*30:25', 'A*30:26', 'A*30:28', 'A*30:29', 'A*30:30', 'A*30:31', 'A*30:32', 'A*30:33',
        'A*30:34', 'A*30:35', 'A*30:36', 'A*30:37', 'A*30:38', 'A*30:39', 'A*30:40', 'A*30:41', 'A*31:01', 'A*31:02',
        'A*31:03', 'A*31:04', 'A*31:05', 'A*31:06', 'A*31:07', 'A*31:08', 'A*31:09', 'A*31:10', 'A*31:11', 'A*31:12',
        'A*31:13', 'A*31:15', 'A*31:16', 'A*31:17', 'A*31:18', 'A*31:19', 'A*31:20', 'A*31:21', 'A*31:22', 'A*31:23',
        'A*31:24', 'A*31:25', 'A*31:26', 'A*31:27', 'A*31:28', 'A*31:29', 'A*31:30', 'A*31:31', 'A*31:32', 'A*31:33',
        'A*31:34', 'A*31:35', 'A*31:36', 'A*31:37', 'A*32:01', 'A*32:02', 'A*32:03', 'A*32:04', 'A*32:05', 'A*32:06',
        'A*32:07', 'A*32:08', 'A*32:09', 'A*32:10', 'A*32:12', 'A*32:13', 'A*32:14', 'A*32:15', 'A*32:16', 'A*32:17',
        'A*32:18', 'A*32:20', 'A*32:21', 'A*32:22', 'A*32:23', 'A*32:24', 'A*32:25', 'A*33:01', 'A*33:03', 'A*33:04',
        'A*33:05', 'A*33:06', 'A*33:07', 'A*33:08', 'A*33:09', 'A*33:10', 'A*33:11', 'A*33:12', 'A*33:13', 'A*33:14',
        'A*33:15', 'A*33:16', 'A*33:17', 'A*33:18', 'A*33:19', 'A*33:20', 'A*33:21', 'A*33:22', 'A*33:23', 'A*33:24',
        'A*33:25', 'A*33:26', 'A*33:27', 'A*33:28', 'A*33:29', 'A*33:30', 'A*33:31', 'A*34:01', 'A*34:02', 'A*34:03',
        'A*34:04', 'A*34:05', 'A*34:06', 'A*34:07', 'A*34:08', 'A*36:01', 'A*36:02', 'A*36:03', 'A*36:04', 'A*36:05',
        'A*43:01', 'A*66:01', 'A*66:02', 'A*66:03', 'A*66:04', 'A*66:05', 'A*66:06', 'A*66:07', 'A*66:08', 'A*66:09',
        'A*66:10', 'A*66:11', 'A*66:12', 'A*66:13', 'A*66:14', 'A*66:15', 'A*68:01', 'A*68:02', 'A*68:03', 'A*68:04',
        'A*68:05', 'A*68:06', 'A*68:07', 'A*68:08', 'A*68:09', 'A*68:10', 'A*68:12', 'A*68:13', 'A*68:14', 'A*68:15',
        'A*68:16', 'A*68:17', 'A*68:19', 'A*68:20', 'A*68:21', 'A*68:22', 'A*68:23', 'A*68:24', 'A*68:25', 'A*68:26',
        'A*68:27', 'A*68:28', 'A*68:29', 'A*68:30', 'A*68:31', 'A*68:32', 'A*68:33', 'A*68:34', 'A*68:35', 'A*68:36',
        'A*68:37', 'A*68:38', 'A*68:39', 'A*68:40', 'A*68:41', 'A*68:42', 'A*68:43', 'A*68:44', 'A*68:45', 'A*68:46',
        'A*68:47', 'A*68:48', 'A*68:50', 'A*68:51', 'A*68:52', 'A*68:53', 'A*68:54', 'A*69:01', 'A*74:01', 'A*74:02',
        'A*74:03', 'A*74:04', 'A*74:05', 'A*74:06', 'A*74:07', 'A*74:08', 'A*74:09', 'A*74:10', 'A*74:11', 'A*74:13',
        'A*80:01', 'A*80:02', 'B*07:02', 'B*07:03', 'B*07:04', 'B*07:05', 'B*07:06', 'B*07:07', 'B*07:08', 'B*07:09',
        'B*07:10', 'B*07:11', 'B*07:12', 'B*07:13', 'B*07:14', 'B*07:15', 'B*07:16', 'B*07:17', 'B*07:18', 'B*07:19',
        'B*07:20', 'B*07:21', 'B*07:22', 'B*07:23', 'B*07:24', 'B*07:25', 'B*07:26', 'B*07:27', 'B*07:28', 'B*07:29',
        'B*07:30', 'B*07:31', 'B*07:32', 'B*07:33', 'B*07:34', 'B*07:35', 'B*07:36', 'B*07:37', 'B*07:38', 'B*07:39',
        'B*07:40', 'B*07:41', 'B*07:42', 'B*07:43', 'B*07:44', 'B*07:45', 'B*07:46', 'B*07:47', 'B*07:48', 'B*07:50',
        'B*07:51', 'B*07:52', 'B*07:53', 'B*07:54', 'B*07:55', 'B*07:56', 'B*07:57', 'B*07:58', 'B*07:59', 'B*07:60',
        'B*07:61', 'B*07:62', 'B*07:63', 'B*07:64', 'B*07:65', 'B*07:66', 'B*07:68', 'B*07:69', 'B*07:70', 'B*07:71',
        'B*07:72', 'B*07:73', 'B*07:74', 'B*07:75', 'B*07:76', 'B*07:77', 'B*07:78', 'B*07:79', 'B*07:80', 'B*07:81',
        'B*07:82', 'B*07:83', 'B*07:84', 'B*07:85', 'B*07:86', 'B*07:87', 'B*07:88', 'B*07:89', 'B*07:90', 'B*07:91',
        'B*07:92', 'B*07:93', 'B*07:94', 'B*07:95', 'B*07:96', 'B*07:97', 'B*07:98', 'B*07:99', 'B*07:100', 'B*07:101',
        'B*07:102', 'B*07:103', 'B*07:104', 'B*07:105', 'B*07:106', 'B*07:107', 'B*07:108', 'B*07:109', 'B*07:110',
        'B*07:112', 'B*07:113', 'B*07:114', 'B*07:115', 'B*08:01', 'B*08:02', 'B*08:03', 'B*08:04', 'B*08:05',
        'B*08:07', 'B*08:09', 'B*08:10', 'B*08:11', 'B*08:12', 'B*08:13', 'B*08:14', 'B*08:15', 'B*08:16', 'B*08:17',
        'B*08:18', 'B*08:20', 'B*08:21', 'B*08:22', 'B*08:23', 'B*08:24', 'B*08:25', 'B*08:26', 'B*08:27', 'B*08:28',
        'B*08:29', 'B*08:31', 'B*08:32', 'B*08:33', 'B*08:34', 'B*08:35', 'B*08:36', 'B*08:37', 'B*08:38', 'B*08:39',
        'B*08:40', 'B*08:41', 'B*08:42', 'B*08:43', 'B*08:44', 'B*08:45', 'B*08:46', 'B*08:47', 'B*08:48', 'B*08:49',
        'B*08:50', 'B*08:51', 'B*08:52', 'B*08:53', 'B*08:54', 'B*08:55', 'B*08:56', 'B*08:57', 'B*08:58', 'B*08:59',
        'B*08:60', 'B*08:61', 'B*08:62', 'B*13:01', 'B*13:02', 'B*13:03', 'B*13:04', 'B*13:06', 'B*13:09', 'B*13:10',
        'B*13:11', 'B*13:12', 'B*13:13', 'B*13:14', 'B*13:15', 'B*13:16', 'B*13:17', 'B*13:18', 'B*13:19', 'B*13:20',
        'B*13:21', 'B*13:22', 'B*13:23', 'B*13:25', 'B*13:26', 'B*13:27', 'B*13:28', 'B*13:29', 'B*13:30', 'B*13:31',
        'B*13:32', 'B*13:33', 'B*13:34', 'B*13:35', 'B*13:36', 'B*13:37', 'B*13:38', 'B*13:39', 'B*14:01', 'B*14:02',
        'B*14:03', 'B*14:04', 'B*14:05', 'B*14:06', 'B*14:08', 'B*14:09', 'B*14:10', 'B*14:11', 'B*14:12', 'B*14:13',
        'B*14:14', 'B*14:15', 'B*14:16', 'B*14:17', 'B*14:18', 'B*15:01', 'B*15:02', 'B*15:03', 'B*15:04', 'B*15:05',
        'B*15:06', 'B*15:07', 'B*15:08', 'B*15:09', 'B*15:10', 'B*15:11', 'B*15:12', 'B*15:13', 'B*15:14', 'B*15:15',
        'B*15:16', 'B*15:17', 'B*15:18', 'B*15:19', 'B*15:20', 'B*15:21', 'B*15:23', 'B*15:24', 'B*15:25', 'B*15:27',
        'B*15:28', 'B*15:29', 'B*15:30', 'B*15:31', 'B*15:32', 'B*15:33', 'B*15:34', 'B*15:35', 'B*15:36', 'B*15:37',
        'B*15:38', 'B*15:39', 'B*15:40', 'B*15:42', 'B*15:43', 'B*15:44', 'B*15:45', 'B*15:46', 'B*15:47', 'B*15:48',
        'B*15:49', 'B*15:50', 'B*15:51', 'B*15:52', 'B*15:53', 'B*15:54', 'B*15:55', 'B*15:56', 'B*15:57', 'B*15:58',
        'B*15:60', 'B*15:61', 'B*15:62', 'B*15:63', 'B*15:64', 'B*15:65', 'B*15:66', 'B*15:67', 'B*15:68', 'B*15:69',
        'B*15:70', 'B*15:71', 'B*15:72', 'B*15:73', 'B*15:74', 'B*15:75', 'B*15:76', 'B*15:77', 'B*15:78', 'B*15:80',
        'B*15:81', 'B*15:82', 'B*15:83', 'B*15:84', 'B*15:85', 'B*15:86', 'B*15:87', 'B*15:88', 'B*15:89', 'B*15:90',
        'B*15:91', 'B*15:92', 'B*15:93', 'B*15:95', 'B*15:96', 'B*15:97', 'B*15:98', 'B*15:99', 'B*15:101', 'B*15:102',
        'B*15:103', 'B*15:104', 'B*15:105', 'B*15:106', 'B*15:107', 'B*15:108', 'B*15:109', 'B*15:110', 'B*15:112',
        'B*15:113', 'B*15:114', 'B*15:115', 'B*15:116', 'B*15:117', 'B*15:118', 'B*15:119', 'B*15:120', 'B*15:121',
        'B*15:122', 'B*15:123', 'B*15:124', 'B*15:125', 'B*15:126', 'B*15:127', 'B*15:128', 'B*15:129', 'B*15:131',
        'B*15:132', 'B*15:133', 'B*15:134', 'B*15:135', 'B*15:136', 'B*15:137', 'B*15:138', 'B*15:139', 'B*15:140',
        'B*15:141', 'B*15:142', 'B*15:143', 'B*15:144', 'B*15:145', 'B*15:146', 'B*15:147', 'B*15:148', 'B*15:150',
        'B*15:151', 'B*15:152', 'B*15:153', 'B*15:154', 'B*15:155', 'B*15:156', 'B*15:157', 'B*15:158', 'B*15:159',
        'B*15:160', 'B*15:161', 'B*15:162', 'B*15:163', 'B*15:164', 'B*15:165', 'B*15:166', 'B*15:167', 'B*15:168',
        'B*15:169', 'B*15:170', 'B*15:171', 'B*15:172', 'B*15:173', 'B*15:174', 'B*15:175', 'B*15:176', 'B*15:177',
        'B*15:178', 'B*15:179', 'B*15:180', 'B*15:183', 'B*15:184', 'B*15:185', 'B*15:186', 'B*15:187', 'B*15:188',
        'B*15:189', 'B*15:191', 'B*15:192', 'B*15:193', 'B*15:194', 'B*15:195', 'B*15:196', 'B*15:197', 'B*15:198',
        'B*15:199', 'B*15:200', 'B*15:201', 'B*15:202', 'B*18:01', 'B*18:02', 'B*18:03', 'B*18:04', 'B*18:05',
        'B*18:06', 'B*18:07', 'B*18:08', 'B*18:09', 'B*18:10', 'B*18:11', 'B*18:12', 'B*18:13', 'B*18:14', 'B*18:15',
        'B*18:18', 'B*18:19', 'B*18:20', 'B*18:21', 'B*18:22', 'B*18:24', 'B*18:25', 'B*18:26', 'B*18:27', 'B*18:28',
        'B*18:29', 'B*18:30', 'B*18:31', 'B*18:32', 'B*18:33', 'B*18:34', 'B*18:35', 'B*18:36', 'B*18:37', 'B*18:38',
        'B*18:39', 'B*18:40', 'B*18:41', 'B*18:42', 'B*18:43', 'B*18:44', 'B*18:45', 'B*18:46', 'B*18:47', 'B*18:48',
        'B*18:49', 'B*18:50', 'B*27:01', 'B*27:02', 'B*27:03', 'B*27:04', 'B*27:05', 'B*27:06', 'B*27:07', 'B*27:08',
        'B*27:09', 'B*27:10', 'B*27:11', 'B*27:12', 'B*27:13', 'B*27:14', 'B*27:15', 'B*27:16', 'B*27:17', 'B*27:18',
        'B*27:19', 'B*27:20', 'B*27:21', 'B*27:23', 'B*27:24', 'B*27:25', 'B*27:26', 'B*27:27', 'B*27:28', 'B*27:29',
        'B*27:30', 'B*27:31', 'B*27:32', 'B*27:33', 'B*27:34', 'B*27:35', 'B*27:36', 'B*27:37', 'B*27:38', 'B*27:39',
        'B*27:40', 'B*27:41', 'B*27:42', 'B*27:43', 'B*27:44', 'B*27:45', 'B*27:46', 'B*27:47', 'B*27:48', 'B*27:49',
        'B*27:50', 'B*27:51', 'B*27:52', 'B*27:53', 'B*27:54', 'B*27:55', 'B*27:56', 'B*27:57', 'B*27:58', 'B*27:60',
        'B*27:61', 'B*27:62', 'B*27:63', 'B*27:67', 'B*27:68', 'B*27:69', 'B*35:01', 'B*35:02', 'B*35:03', 'B*35:04',
        'B*35:05', 'B*35:06', 'B*35:07', 'B*35:08', 'B*35:09', 'B*35:10', 'B*35:11', 'B*35:12', 'B*35:13', 'B*35:14',
        'B*35:15', 'B*35:16', 'B*35:17', 'B*35:18', 'B*35:19', 'B*35:20', 'B*35:21', 'B*35:22', 'B*35:23', 'B*35:24',
        'B*35:25', 'B*35:26', 'B*35:27', 'B*35:28', 'B*35:29', 'B*35:30', 'B*35:31', 'B*35:32', 'B*35:33', 'B*35:34',
        'B*35:35', 'B*35:36', 'B*35:37', 'B*35:38', 'B*35:39', 'B*35:41', 'B*35:42', 'B*35:43', 'B*35:44', 'B*35:45',
        'B*35:46', 'B*35:47', 'B*35:48', 'B*35:49', 'B*35:50', 'B*35:51', 'B*35:52', 'B*35:54', 'B*35:55', 'B*35:56',
        'B*35:57', 'B*35:58', 'B*35:59', 'B*35:60', 'B*35:61', 'B*35:62', 'B*35:63', 'B*35:64', 'B*35:66', 'B*35:67',
        'B*35:68', 'B*35:69', 'B*35:70', 'B*35:71', 'B*35:72', 'B*35:74', 'B*35:75', 'B*35:76', 'B*35:77', 'B*35:78',
        'B*35:79', 'B*35:80', 'B*35:81', 'B*35:82', 'B*35:83', 'B*35:84', 'B*35:85', 'B*35:86', 'B*35:87', 'B*35:88',
        'B*35:89', 'B*35:90', 'B*35:91', 'B*35:92', 'B*35:93', 'B*35:94', 'B*35:95', 'B*35:96', 'B*35:97', 'B*35:98',
        'B*35:99', 'B*35:100', 'B*35:101', 'B*35:102', 'B*35:103', 'B*35:104', 'B*35:105', 'B*35:106', 'B*35:107',
        'B*35:108', 'B*35:109', 'B*35:110', 'B*35:111', 'B*35:112', 'B*35:113', 'B*35:114', 'B*35:115', 'B*35:116',
        'B*35:117', 'B*35:118', 'B*35:119', 'B*35:120', 'B*35:121', 'B*35:122', 'B*35:123', 'B*35:124', 'B*35:125',
        'B*35:126', 'B*35:127', 'B*35:128', 'B*35:131', 'B*35:132', 'B*35:133', 'B*35:135', 'B*35:136', 'B*35:137',
        'B*35:138', 'B*35:139', 'B*35:140', 'B*35:141', 'B*35:142', 'B*35:143', 'B*35:144', 'B*37:01', 'B*37:02',
        'B*37:04', 'B*37:05', 'B*37:06', 'B*37:07', 'B*37:08', 'B*37:09', 'B*37:10', 'B*37:11', 'B*37:12', 'B*37:13',
        'B*37:14', 'B*37:15', 'B*37:17', 'B*37:18', 'B*37:19', 'B*37:20', 'B*37:21', 'B*37:22', 'B*37:23', 'B*38:01',
        'B*38:02', 'B*38:03', 'B*38:04', 'B*38:05', 'B*38:06', 'B*38:07', 'B*38:08', 'B*38:09', 'B*38:10', 'B*38:11',
        'B*38:12', 'B*38:13', 'B*38:14', 'B*38:15', 'B*38:16', 'B*38:17', 'B*38:18', 'B*38:19', 'B*38:20', 'B*38:21',
        'B*38:22', 'B*38:23', 'B*39:01', 'B*39:02', 'B*39:03', 'B*39:04', 'B*39:05', 'B*39:06', 'B*39:07', 'B*39:08',
        'B*39:09', 'B*39:10', 'B*39:11', 'B*39:12', 'B*39:13', 'B*39:14', 'B*39:15', 'B*39:16', 'B*39:17', 'B*39:18',
        'B*39:19', 'B*39:20', 'B*39:22', 'B*39:23', 'B*39:24', 'B*39:26', 'B*39:27', 'B*39:28', 'B*39:29', 'B*39:30',
        'B*39:31', 'B*39:32', 'B*39:33', 'B*39:34', 'B*39:35', 'B*39:36', 'B*39:37', 'B*39:39', 'B*39:41', 'B*39:42',
        'B*39:43', 'B*39:44', 'B*39:45', 'B*39:46', 'B*39:47', 'B*39:48', 'B*39:49', 'B*39:50', 'B*39:51', 'B*39:52',
        'B*39:53', 'B*39:54', 'B*39:55', 'B*39:56', 'B*39:57', 'B*39:58', 'B*39:59', 'B*39:60', 'B*40:01', 'B*40:02',
        'B*40:03', 'B*40:04', 'B*40:05', 'B*40:06', 'B*40:07', 'B*40:08', 'B*40:09', 'B*40:10', 'B*40:11', 'B*40:12',
        'B*40:13', 'B*40:14', 'B*40:15', 'B*40:16', 'B*40:18', 'B*40:19', 'B*40:20', 'B*40:21', 'B*40:23', 'B*40:24',
        'B*40:25', 'B*40:26', 'B*40:27', 'B*40:28', 'B*40:29', 'B*40:30', 'B*40:31', 'B*40:32', 'B*40:33', 'B*40:34',
        'B*40:35', 'B*40:36', 'B*40:37', 'B*40:38', 'B*40:39', 'B*40:40', 'B*40:42', 'B*40:43', 'B*40:44', 'B*40:45',
        'B*40:46', 'B*40:47', 'B*40:48', 'B*40:49', 'B*40:50', 'B*40:51', 'B*40:52', 'B*40:53', 'B*40:54', 'B*40:55',
        'B*40:56', 'B*40:57', 'B*40:58', 'B*40:59', 'B*40:60', 'B*40:61', 'B*40:62', 'B*40:63', 'B*40:64', 'B*40:65',
        'B*40:66', 'B*40:67', 'B*40:68', 'B*40:69', 'B*40:70', 'B*40:71', 'B*40:72', 'B*40:73', 'B*40:74', 'B*40:75',
        'B*40:76', 'B*40:77', 'B*40:78', 'B*40:79', 'B*40:80', 'B*40:81', 'B*40:82', 'B*40:83', 'B*40:84', 'B*40:85',
        'B*40:86', 'B*40:87', 'B*40:88', 'B*40:89', 'B*40:90', 'B*40:91', 'B*40:92', 'B*40:93', 'B*40:94', 'B*40:95',
        'B*40:96', 'B*40:97', 'B*40:98', 'B*40:99', 'B*40:100', 'B*40:101', 'B*40:102', 'B*40:103', 'B*40:104',
        'B*40:105', 'B*40:106', 'B*40:107', 'B*40:108', 'B*40:109', 'B*40:110', 'B*40:111', 'B*40:112', 'B*40:113',
        'B*40:114', 'B*40:115', 'B*40:116', 'B*40:117', 'B*40:119', 'B*40:120', 'B*40:121', 'B*40:122', 'B*40:123',
        'B*40:124', 'B*40:125', 'B*40:126', 'B*40:127', 'B*40:128', 'B*40:129', 'B*40:130', 'B*40:131', 'B*40:132',
        'B*40:134', 'B*40:135', 'B*40:136', 'B*40:137', 'B*40:138', 'B*40:139', 'B*40:140', 'B*40:141', 'B*40:143',
        'B*40:145', 'B*40:146', 'B*40:147', 'B*41:01', 'B*41:02', 'B*41:03', 'B*41:04', 'B*41:05', 'B*41:06', 'B*41:07',
        'B*41:08', 'B*41:09', 'B*41:10', 'B*41:11', 'B*41:12', 'B*42:01', 'B*42:02', 'B*42:04', 'B*42:05', 'B*42:06',
        'B*42:07', 'B*42:08', 'B*42:09', 'B*42:10', 'B*42:11', 'B*42:12', 'B*42:13', 'B*42:14', 'B*44:02', 'B*44:03',
        'B*44:04', 'B*44:05', 'B*44:06', 'B*44:07', 'B*44:08', 'B*44:09', 'B*44:10', 'B*44:11', 'B*44:12', 'B*44:13',
        'B*44:14', 'B*44:15', 'B*44:16', 'B*44:17', 'B*44:18', 'B*44:20', 'B*44:21', 'B*44:22', 'B*44:24', 'B*44:25',
        'B*44:26', 'B*44:27', 'B*44:28', 'B*44:29', 'B*44:30', 'B*44:31', 'B*44:32', 'B*44:33', 'B*44:34', 'B*44:35',
        'B*44:36', 'B*44:37', 'B*44:38', 'B*44:39', 'B*44:40', 'B*44:41', 'B*44:42', 'B*44:43', 'B*44:44', 'B*44:45',
        'B*44:46', 'B*44:47', 'B*44:48', 'B*44:49', 'B*44:50', 'B*44:51', 'B*44:53', 'B*44:54', 'B*44:55', 'B*44:57',
        'B*44:59', 'B*44:60', 'B*44:62', 'B*44:63', 'B*44:64', 'B*44:65', 'B*44:66', 'B*44:67', 'B*44:68', 'B*44:69',
        'B*44:70', 'B*44:71', 'B*44:72', 'B*44:73', 'B*44:74', 'B*44:75', 'B*44:76', 'B*44:77', 'B*44:78', 'B*44:79',
        'B*44:80', 'B*44:81', 'B*44:82', 'B*44:83', 'B*44:84', 'B*44:85', 'B*44:86', 'B*44:87', 'B*44:88', 'B*44:89',
        'B*44:90', 'B*44:91', 'B*44:92', 'B*44:93', 'B*44:94', 'B*44:95', 'B*44:96', 'B*44:97', 'B*44:98', 'B*44:99',
        'B*44:100', 'B*44:101', 'B*44:102', 'B*44:103', 'B*44:104', 'B*44:105', 'B*44:106', 'B*44:107', 'B*44:109',
        'B*44:110', 'B*45:01', 'B*45:02', 'B*45:03', 'B*45:04', 'B*45:05', 'B*45:06', 'B*45:07', 'B*45:08', 'B*45:09',
        'B*45:10', 'B*45:11', 'B*45:12', 'B*46:01', 'B*46:02', 'B*46:03', 'B*46:04', 'B*46:05', 'B*46:06', 'B*46:08',
        'B*46:09', 'B*46:10', 'B*46:11', 'B*46:12', 'B*46:13', 'B*46:14', 'B*46:16', 'B*46:17', 'B*46:18', 'B*46:19',
        'B*46:20', 'B*46:21', 'B*46:22', 'B*46:23', 'B*46:24', 'B*47:01', 'B*47:02', 'B*47:03', 'B*47:04', 'B*47:05',
        'B*47:06', 'B*47:07', 'B*48:01', 'B*48:02', 'B*48:03', 'B*48:04', 'B*48:05', 'B*48:06', 'B*48:07', 'B*48:08',
        'B*48:09', 'B*48:10', 'B*48:11', 'B*48:12', 'B*48:13', 'B*48:14', 'B*48:15', 'B*48:16', 'B*48:17', 'B*48:18',
        'B*48:19', 'B*48:20', 'B*48:21', 'B*48:22', 'B*48:23', 'B*49:01', 'B*49:02', 'B*49:03', 'B*49:04', 'B*49:05',
        'B*49:06', 'B*49:07', 'B*49:08', 'B*49:09', 'B*49:10', 'B*50:01', 'B*50:02', 'B*50:04', 'B*50:05', 'B*50:06',
        'B*50:07', 'B*50:08', 'B*50:09', 'B*51:01', 'B*51:02', 'B*51:03', 'B*51:04', 'B*51:05', 'B*51:06', 'B*51:07',
        'B*51:08', 'B*51:09', 'B*51:12', 'B*51:13', 'B*51:14', 'B*51:15', 'B*51:16', 'B*51:17', 'B*51:18', 'B*51:19',
        'B*51:20', 'B*51:21', 'B*51:22', 'B*51:23', 'B*51:24', 'B*51:26', 'B*51:28', 'B*51:29', 'B*51:30', 'B*51:31',
        'B*51:32', 'B*51:33', 'B*51:34', 'B*51:35', 'B*51:36', 'B*51:37', 'B*51:38', 'B*51:39', 'B*51:40', 'B*51:42',
        'B*51:43', 'B*51:45', 'B*51:46', 'B*51:48', 'B*51:49', 'B*51:50', 'B*51:51', 'B*51:52', 'B*51:53', 'B*51:54',
        'B*51:55', 'B*51:56', 'B*51:57', 'B*51:58', 'B*51:59', 'B*51:60', 'B*51:61', 'B*51:62', 'B*51:63', 'B*51:64',
        'B*51:65', 'B*51:66', 'B*51:67', 'B*51:68', 'B*51:69', 'B*51:70', 'B*51:71', 'B*51:72', 'B*51:73', 'B*51:74',
        'B*51:75', 'B*51:76', 'B*51:77', 'B*51:78', 'B*51:79', 'B*51:80', 'B*51:81', 'B*51:82', 'B*51:83', 'B*51:84',
        'B*51:85', 'B*51:86', 'B*51:87', 'B*51:88', 'B*51:89', 'B*51:90', 'B*51:91', 'B*51:92', 'B*51:93', 'B*51:94',
        'B*51:95', 'B*51:96', 'B*52:01', 'B*52:02', 'B*52:03', 'B*52:04', 'B*52:05', 'B*52:06', 'B*52:07', 'B*52:08',
        'B*52:09', 'B*52:10', 'B*52:11', 'B*52:12', 'B*52:13', 'B*52:14', 'B*52:15', 'B*52:16', 'B*52:17', 'B*52:18',
        'B*52:19', 'B*52:20', 'B*52:21', 'B*53:01', 'B*53:02', 'B*53:03', 'B*53:04', 'B*53:05', 'B*53:06', 'B*53:07',
        'B*53:08', 'B*53:09', 'B*53:10', 'B*53:11', 'B*53:12', 'B*53:13', 'B*53:14', 'B*53:15', 'B*53:16', 'B*53:17',
        'B*53:18', 'B*53:19', 'B*53:20', 'B*53:21', 'B*53:22', 'B*53:23', 'B*54:01', 'B*54:02', 'B*54:03', 'B*54:04',
        'B*54:06', 'B*54:07', 'B*54:09', 'B*54:10', 'B*54:11', 'B*54:12', 'B*54:13', 'B*54:14', 'B*54:15', 'B*54:16',
        'B*54:17', 'B*54:18', 'B*54:19', 'B*54:20', 'B*54:21', 'B*54:22', 'B*54:23', 'B*55:01', 'B*55:02', 'B*55:03',
        'B*55:04', 'B*55:05', 'B*55:07', 'B*55:08', 'B*55:09', 'B*55:10', 'B*55:11', 'B*55:12', 'B*55:13', 'B*55:14',
        'B*55:15', 'B*55:16', 'B*55:17', 'B*55:18', 'B*55:19', 'B*55:20', 'B*55:21', 'B*55:22', 'B*55:23', 'B*55:24',
        'B*55:25', 'B*55:26', 'B*55:27', 'B*55:28', 'B*55:29', 'B*55:30', 'B*55:31', 'B*55:32', 'B*55:33', 'B*55:34',
        'B*55:35', 'B*55:36', 'B*55:37', 'B*55:38', 'B*55:39', 'B*55:40', 'B*55:41', 'B*55:42', 'B*55:43', 'B*56:01',
        'B*56:02', 'B*56:03', 'B*56:04', 'B*56:05', 'B*56:06', 'B*56:07', 'B*56:08', 'B*56:09', 'B*56:10', 'B*56:11',
        'B*56:12', 'B*56:13', 'B*56:14', 'B*56:15', 'B*56:16', 'B*56:17', 'B*56:18', 'B*56:20', 'B*56:21', 'B*56:22',
        'B*56:23', 'B*56:24', 'B*56:25', 'B*56:26', 'B*56:27', 'B*56:29', 'B*57:01', 'B*57:02', 'B*57:03', 'B*57:04',
        'B*57:05', 'B*57:06', 'B*57:07', 'B*57:08', 'B*57:09', 'B*57:10', 'B*57:11', 'B*57:12', 'B*57:13', 'B*57:14',
        'B*57:15', 'B*57:16', 'B*57:17', 'B*57:18', 'B*57:19', 'B*57:20', 'B*57:21', 'B*57:22', 'B*57:23', 'B*57:24',
        'B*57:25', 'B*57:26', 'B*57:27', 'B*57:29', 'B*57:30', 'B*57:31', 'B*57:32', 'B*58:01', 'B*58:02', 'B*58:04',
        'B*58:05', 'B*58:06', 'B*58:07', 'B*58:08', 'B*58:09', 'B*58:11', 'B*58:12', 'B*58:13', 'B*58:14', 'B*58:15',
        'B*58:16', 'B*58:18', 'B*58:19', 'B*58:20', 'B*58:21', 'B*58:22', 'B*58:23', 'B*58:24', 'B*58:25', 'B*58:26',
        'B*58:27', 'B*58:28', 'B*58:29', 'B*58:30', 'B*59:01', 'B*59:02', 'B*59:03', 'B*59:04', 'B*59:05', 'B*67:01',
        'B*67:02', 'B*73:01', 'B*73:02', 'B*78:01', 'B*78:02', 'B*78:03', 'B*78:04', 'B*78:05', 'B*78:06', 'B*78:07',
        'B*81:01', 'B*81:02', 'B*81:03', 'B*81:05', 'B*82:01', 'B*82:02', 'B*82:03', 'B*83:01', 'C*01:02', 'C*01:03',
        'C*01:04', 'C*01:05', 'C*01:06', 'C*01:07', 'C*01:08', 'C*01:09', 'C*01:10', 'C*01:11', 'C*01:12', 'C*01:13',
        'C*01:14', 'C*01:15', 'C*01:16', 'C*01:17', 'C*01:18', 'C*01:19', 'C*01:20', 'C*01:21', 'C*01:22', 'C*01:23',
        'C*01:24', 'C*01:25', 'C*01:26', 'C*01:27', 'C*01:28', 'C*01:29', 'C*01:30', 'C*01:31', 'C*01:32', 'C*01:33',
        'C*01:34', 'C*01:35', 'C*01:36', 'C*01:38', 'C*01:39', 'C*01:40', 'C*02:02', 'C*02:03', 'C*02:04', 'C*02:05',
        'C*02:06', 'C*02:07', 'C*02:08', 'C*02:09', 'C*02:10', 'C*02:11', 'C*02:12', 'C*02:13', 'C*02:14', 'C*02:15',
        'C*02:16', 'C*02:17', 'C*02:18', 'C*02:19', 'C*02:20', 'C*02:21', 'C*02:22', 'C*02:23', 'C*02:24', 'C*02:26',
        'C*02:27', 'C*02:28', 'C*02:29', 'C*02:30', 'C*02:31', 'C*02:32', 'C*02:33', 'C*02:34', 'C*02:35', 'C*02:36',
        'C*02:37', 'C*02:39', 'C*02:40', 'C*03:01', 'C*03:02', 'C*03:03', 'C*03:04', 'C*03:05', 'C*03:06', 'C*03:07',
        'C*03:08', 'C*03:09', 'C*03:10', 'C*03:11', 'C*03:12', 'C*03:13', 'C*03:14', 'C*03:15', 'C*03:16', 'C*03:17',
        'C*03:18', 'C*03:19', 'C*03:21', 'C*03:23', 'C*03:24', 'C*03:25', 'C*03:26', 'C*03:27', 'C*03:28', 'C*03:29',
        'C*03:30', 'C*03:31', 'C*03:32', 'C*03:33', 'C*03:34', 'C*03:35', 'C*03:36', 'C*03:37', 'C*03:38', 'C*03:39',
        'C*03:40', 'C*03:41', 'C*03:42', 'C*03:43', 'C*03:44', 'C*03:45', 'C*03:46', 'C*03:47', 'C*03:48', 'C*03:49',
        'C*03:50', 'C*03:51', 'C*03:52', 'C*03:53', 'C*03:54', 'C*03:55', 'C*03:56', 'C*03:57', 'C*03:58', 'C*03:59',
        'C*03:60', 'C*03:61', 'C*03:62', 'C*03:63', 'C*03:64', 'C*03:65', 'C*03:66', 'C*03:67', 'C*03:68', 'C*03:69',
        'C*03:70', 'C*03:71', 'C*03:72', 'C*03:73', 'C*03:74', 'C*03:75', 'C*03:76', 'C*03:77', 'C*03:78', 'C*03:79',
        'C*03:80', 'C*03:81', 'C*03:82', 'C*03:83', 'C*03:84', 'C*03:85', 'C*03:86', 'C*03:87', 'C*03:88', 'C*03:89',
        'C*03:90', 'C*03:91', 'C*03:92', 'C*03:93', 'C*03:94', 'C*04:01', 'C*04:03', 'C*04:04', 'C*04:05', 'C*04:06',
        'C*04:07', 'C*04:08', 'C*04:10', 'C*04:11', 'C*04:12', 'C*04:13', 'C*04:14', 'C*04:15', 'C*04:16', 'C*04:17',
        'C*04:18', 'C*04:19', 'C*04:20', 'C*04:23', 'C*04:24', 'C*04:25', 'C*04:26', 'C*04:27', 'C*04:28', 'C*04:29',
        'C*04:30', 'C*04:31', 'C*04:32', 'C*04:33', 'C*04:34', 'C*04:35', 'C*04:36', 'C*04:37', 'C*04:38', 'C*04:39',
        'C*04:40', 'C*04:41', 'C*04:42', 'C*04:43', 'C*04:44', 'C*04:45', 'C*04:46', 'C*04:47', 'C*04:48', 'C*04:49',
        'C*04:50', 'C*04:51', 'C*04:52', 'C*04:53', 'C*04:54', 'C*04:55', 'C*04:56', 'C*04:57', 'C*04:58', 'C*04:60',
        'C*04:61', 'C*04:62', 'C*04:63', 'C*04:64', 'C*04:65', 'C*04:66', 'C*04:67', 'C*04:68', 'C*04:69', 'C*04:70',
        'C*05:01', 'C*05:03', 'C*05:04', 'C*05:05', 'C*05:06', 'C*05:08', 'C*05:09', 'C*05:10', 'C*05:11', 'C*05:12',
        'C*05:13', 'C*05:14', 'C*05:15', 'C*05:16', 'C*05:17', 'C*05:18', 'C*05:19', 'C*05:20', 'C*05:21', 'C*05:22',
        'C*05:23', 'C*05:24', 'C*05:25', 'C*05:26', 'C*05:27', 'C*05:28', 'C*05:29', 'C*05:30', 'C*05:31', 'C*05:32',
        'C*05:33', 'C*05:34', 'C*05:35', 'C*05:36', 'C*05:37', 'C*05:38', 'C*05:39', 'C*05:40', 'C*05:41', 'C*05:42',
        'C*05:43', 'C*05:44', 'C*05:45', 'C*06:02', 'C*06:03', 'C*06:04', 'C*06:05', 'C*06:06', 'C*06:07', 'C*06:08',
        'C*06:09', 'C*06:10', 'C*06:11', 'C*06:12', 'C*06:13', 'C*06:14', 'C*06:15', 'C*06:17', 'C*06:18', 'C*06:19',
        'C*06:20', 'C*06:21', 'C*06:22', 'C*06:23', 'C*06:24', 'C*06:25', 'C*06:26', 'C*06:27', 'C*06:28', 'C*06:29',
        'C*06:30', 'C*06:31', 'C*06:32', 'C*06:33', 'C*06:34', 'C*06:35', 'C*06:36', 'C*06:37', 'C*06:38', 'C*06:39',
        'C*06:40', 'C*06:41', 'C*06:42', 'C*06:43', 'C*06:44', 'C*06:45', 'C*07:01', 'C*07:02', 'C*07:03', 'C*07:04',
        'C*07:05', 'C*07:06', 'C*07:07', 'C*07:08', 'C*07:09', 'C*07:10', 'C*07:11', 'C*07:12', 'C*07:13', 'C*07:14',
        'C*07:15', 'C*07:16', 'C*07:17', 'C*07:18', 'C*07:19', 'C*07:20', 'C*07:21', 'C*07:22', 'C*07:23', 'C*07:24',
        'C*07:25', 'C*07:26', 'C*07:27', 'C*07:28', 'C*07:29', 'C*07:30', 'C*07:31', 'C*07:35', 'C*07:36', 'C*07:37',
        'C*07:38', 'C*07:39', 'C*07:40', 'C*07:41', 'C*07:42', 'C*07:43', 'C*07:44', 'C*07:45', 'C*07:46', 'C*07:47',
        'C*07:48', 'C*07:49', 'C*07:50', 'C*07:51', 'C*07:52', 'C*07:53', 'C*07:54', 'C*07:56', 'C*07:57', 'C*07:58',
        'C*07:59', 'C*07:60', 'C*07:62', 'C*07:63', 'C*07:64', 'C*07:65', 'C*07:66', 'C*07:67', 'C*07:68', 'C*07:69',
        'C*07:70', 'C*07:71', 'C*07:72', 'C*07:73', 'C*07:74', 'C*07:75', 'C*07:76', 'C*07:77', 'C*07:78', 'C*07:79',
        'C*07:80', 'C*07:81', 'C*07:82', 'C*07:83', 'C*07:84', 'C*07:85', 'C*07:86', 'C*07:87', 'C*07:88', 'C*07:89',
        'C*07:90', 'C*07:91', 'C*07:92', 'C*07:93', 'C*07:94', 'C*07:95', 'C*07:96', 'C*07:97', 'C*07:99', 'C*07:100',
        'C*07:101', 'C*07:102', 'C*07:103', 'C*07:105', 'C*07:106', 'C*07:107', 'C*07:108', 'C*07:109', 'C*07:110',
        'C*07:111', 'C*07:112', 'C*07:113', 'C*07:114', 'C*07:115', 'C*07:116', 'C*07:117', 'C*07:118', 'C*07:119',
        'C*07:120', 'C*07:122', 'C*07:123', 'C*07:124', 'C*07:125', 'C*07:126', 'C*07:127', 'C*07:128', 'C*07:129',
        'C*07:130', 'C*07:131', 'C*07:132', 'C*07:133', 'C*07:134', 'C*07:135', 'C*07:136', 'C*07:137', 'C*07:138',
        'C*07:139', 'C*07:140', 'C*07:141', 'C*07:142', 'C*07:143', 'C*07:144', 'C*07:145', 'C*07:146', 'C*07:147',
        'C*07:148', 'C*07:149', 'C*08:01', 'C*08:02', 'C*08:03', 'C*08:04', 'C*08:05', 'C*08:06', 'C*08:07', 'C*08:08',
        'C*08:09', 'C*08:10', 'C*08:11', 'C*08:12', 'C*08:13', 'C*08:14', 'C*08:15', 'C*08:16', 'C*08:17', 'C*08:18',
        'C*08:19', 'C*08:20', 'C*08:21', 'C*08:22', 'C*08:23', 'C*08:24', 'C*08:25', 'C*08:27', 'C*08:28', 'C*08:29',
        'C*08:30', 'C*08:31', 'C*08:32', 'C*08:33', 'C*08:34', 'C*08:35', 'C*12:02', 'C*12:03', 'C*12:04', 'C*12:05',
        'C*12:06', 'C*12:07', 'C*12:08', 'C*12:09', 'C*12:10', 'C*12:11', 'C*12:12', 'C*12:13', 'C*12:14', 'C*12:15',
        'C*12:16', 'C*12:17', 'C*12:18', 'C*12:19', 'C*12:20', 'C*12:21', 'C*12:22', 'C*12:23', 'C*12:24', 'C*12:25',
        'C*12:26', 'C*12:27', 'C*12:28', 'C*12:29', 'C*12:30', 'C*12:31', 'C*12:32', 'C*12:33', 'C*12:34', 'C*12:35',
        'C*12:36', 'C*12:37', 'C*12:38', 'C*12:40', 'C*12:41', 'C*12:43', 'C*12:44', 'C*14:02', 'C*14:03', 'C*14:04',
        'C*14:05', 'C*14:06', 'C*14:08', 'C*14:09', 'C*14:10', 'C*14:11', 'C*14:12', 'C*14:13', 'C*14:14', 'C*14:15',
        'C*14:16', 'C*14:17', 'C*14:18', 'C*14:19', 'C*14:20', 'C*15:02', 'C*15:03', 'C*15:04', 'C*15:05', 'C*15:06',
        'C*15:07', 'C*15:08', 'C*15:09', 'C*15:10', 'C*15:11', 'C*15:12', 'C*15:13', 'C*15:15', 'C*15:16', 'C*15:17',
        'C*15:18', 'C*15:19', 'C*15:20', 'C*15:21', 'C*15:22', 'C*15:23', 'C*15:24', 'C*15:25', 'C*15:26', 'C*15:27',
        'C*15:28', 'C*15:29', 'C*15:30', 'C*15:31', 'C*15:33', 'C*15:34', 'C*15:35', 'C*16:01', 'C*16:02', 'C*16:04',
        'C*16:06', 'C*16:07', 'C*16:08', 'C*16:09', 'C*16:10', 'C*16:11', 'C*16:12', 'C*16:13', 'C*16:14', 'C*16:15',
        'C*16:17', 'C*16:18', 'C*16:19', 'C*16:20', 'C*16:21', 'C*16:22', 'C*16:23', 'C*16:24', 'C*16:25', 'C*16:26',
        'C*17:01', 'C*17:02', 'C*17:03', 'C*17:04', 'C*17:05', 'C*17:06', 'C*17:07', 'C*18:01', 'C*18:02', 'C*18:03',
        'G*01:01', 'G*01:02', 'G*01:03', 'G*01:04', 'G*01:06', 'G*01:07', 'G*01:08', 'G*01:09', 'E*01:01'])
    __version = "1.1"

    @property
    def version(self):
        return self.__version

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    @property
    def supportedAlleles(self):
        return self.__supported_alleles

    @property
    def name(self):
        return self.__name

    def convert_alleles(self, alleles):
        return ["HLA-%s%s:%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        with open(_file, "r") as f:
            for row in f:
                if row[0] in ["#", "-"] or row.strip() == "" or "pos" in row:
                    continue
                else:
                    s = row.split()
                    result[s[1].replace("*", "")][s[2]] = float(s[4])
        return result

    def get_external_version(self, path=None):
        #Undertermined pickpocket does not support --version or something similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(_input))


class NetCTLpan_1_1(AExternalEpitopePrediction):
    """
    Interface for NetCTLpan 1.1

    NetCTLpan - Pan-specific MHC class I epitope predictions
    Stranzl T., Larsen M. V., Lundegaard C., Nielsen M.
    Immunogenetics. 2010 Apr 9. [Epub ahead of print]
    """
    __name = "netctlpan"
    __command = "netctlpan -f {peptides} -a {alleles} {options} > {out}"
    __supported_length = frozenset([8, 9, 10, 11])
    __alleles = frozenset(['A*01:01', 'A*01:02', 'A*01:03', 'A*01:06', 'A*01:07', 'A*01:08', 'A*01:09', 'A*01:10', 'A*01:12',
                 'A*01:13', 'A*01:14', 'A*01:17', 'A*01:19', 'A*01:20', 'A*01:21', 'A*01:23', 'A*01:24', 'A*01:25',
                 'A*01:26', 'A*01:28', 'A*01:29', 'A*01:30', 'A*01:32', 'A*01:33', 'A*01:35', 'A*01:36', 'A*01:37',
                 'A*01:38', 'A*01:39', 'A*01:40', 'A*01:41', 'A*01:42', 'A*01:43', 'A*01:44', 'A*01:45', 'A*01:46',
                 'A*01:47', 'A*01:48', 'A*01:49', 'A*01:50', 'A*01:51', 'A*01:54', 'A*01:55', 'A*01:58', 'A*01:59',
                 'A*01:60', 'A*01:61', 'A*01:62', 'A*01:63', 'A*01:64', 'A*01:65', 'A*01:66', 'A*02:01', 'A*02:02',
                 'A*02:03', 'A*02:04', 'A*02:05', 'A*02:06', 'A*02:07', 'A*02:08', 'A*02:09', 'A*02:10', 'A*02:101',
                 'A*02:102', 'A*02:103', 'A*02:104', 'A*02:105', 'A*02:106', 'A*02:107', 'A*02:108', 'A*02:109',
                 'A*02:11', 'A*02:110', 'A*02:111', 'A*02:112', 'A*02:114', 'A*02:115', 'A*02:116', 'A*02:117',
                 'A*02:118', 'A*02:119', 'A*02:12', 'A*02:120', 'A*02:121', 'A*02:122', 'A*02:123', 'A*02:124',
                 'A*02:126', 'A*02:127', 'A*02:128', 'A*02:129', 'A*02:13', 'A*02:130', 'A*02:131', 'A*02:132',
                 'A*02:133', 'A*02:134', 'A*02:135', 'A*02:136', 'A*02:137', 'A*02:138', 'A*02:139', 'A*02:14',
                 'A*02:140', 'A*02:141', 'A*02:142', 'A*02:143', 'A*02:144', 'A*02:145', 'A*02:146', 'A*02:147',
                 'A*02:148', 'A*02:149', 'A*02:150', 'A*02:151', 'A*02:152', 'A*02:153', 'A*02:154', 'A*02:155',
                 'A*02:156', 'A*02:157', 'A*02:158', 'A*02:159', 'A*02:16', 'A*02:160', 'A*02:161', 'A*02:162',
                 'A*02:163', 'A*02:164', 'A*02:165', 'A*02:166', 'A*02:167', 'A*02:168', 'A*02:169', 'A*02:17',
                 'A*02:170', 'A*02:171', 'A*02:172', 'A*02:173', 'A*02:174', 'A*02:175', 'A*02:176', 'A*02:177',
                 'A*02:178', 'A*02:179', 'A*02:18', 'A*02:180', 'A*02:181', 'A*02:182', 'A*02:183', 'A*02:184',
                 'A*02:185', 'A*02:186', 'A*02:187', 'A*02:188', 'A*02:189', 'A*02:19', 'A*02:190', 'A*02:191',
                 'A*02:192', 'A*02:193', 'A*02:194', 'A*02:195', 'A*02:196', 'A*02:197', 'A*02:198', 'A*02:199',
                 'A*02:20', 'A*02:200', 'A*02:201', 'A*02:202', 'A*02:203', 'A*02:204', 'A*02:205', 'A*02:206',
                 'A*02:207', 'A*02:208', 'A*02:209', 'A*02:21', 'A*02:210', 'A*02:211', 'A*02:212', 'A*02:213',
                 'A*02:214', 'A*02:215', 'A*02:216', 'A*02:217', 'A*02:218', 'A*02:219', 'A*02:22', 'A*02:220',
                 'A*02:221', 'A*02:224', 'A*02:228', 'A*02:229', 'A*02:230', 'A*02:231', 'A*02:232', 'A*02:233',
                 'A*02:234', 'A*02:235', 'A*02:236', 'A*02:237', 'A*02:238', 'A*02:239', 'A*02:24', 'A*02:240',
                 'A*02:241', 'A*02:242', 'A*02:243', 'A*02:244', 'A*02:245', 'A*02:246', 'A*02:247', 'A*02:248',
                 'A*02:249', 'A*02:25', 'A*02:251', 'A*02:252', 'A*02:253', 'A*02:254', 'A*02:255', 'A*02:256',
                 'A*02:257', 'A*02:258', 'A*02:259', 'A*02:26', 'A*02:260', 'A*02:261', 'A*02:262', 'A*02:263',
                 'A*02:264', 'A*02:265', 'A*02:266', 'A*02:27', 'A*02:28', 'A*02:29', 'A*02:30', 'A*02:31', 'A*02:33',
                 'A*02:34', 'A*02:35', 'A*02:36', 'A*02:37', 'A*02:38', 'A*02:39', 'A*02:40', 'A*02:41', 'A*02:42',
                 'A*02:44', 'A*02:45', 'A*02:46', 'A*02:47', 'A*02:48', 'A*02:49', 'A*02:50', 'A*02:51', 'A*02:52',
                 'A*02:54', 'A*02:55', 'A*02:56', 'A*02:57', 'A*02:58', 'A*02:59', 'A*02:60', 'A*02:61', 'A*02:62',
                 'A*02:63', 'A*02:64', 'A*02:65', 'A*02:66', 'A*02:67', 'A*02:68', 'A*02:69', 'A*02:70', 'A*02:71',
                 'A*02:72', 'A*02:73', 'A*02:74', 'A*02:75', 'A*02:76', 'A*02:77', 'A*02:78', 'A*02:79', 'A*02:80',
                 'A*02:81', 'A*02:84', 'A*02:85', 'A*02:86', 'A*02:87', 'A*02:89', 'A*02:90', 'A*02:91', 'A*02:92',
                 'A*02:93', 'A*02:95', 'A*02:96', 'A*02:97', 'A*02:99', 'A*03:01', 'A*03:02', 'A*03:04', 'A*03:05',
                 'A*03:06', 'A*03:07', 'A*03:08', 'A*03:09', 'A*03:10', 'A*03:12', 'A*03:13', 'A*03:14', 'A*03:15',
                 'A*03:16', 'A*03:17', 'A*03:18', 'A*03:19', 'A*03:20', 'A*03:22', 'A*03:23', 'A*03:24', 'A*03:25',
                 'A*03:26', 'A*03:27', 'A*03:28', 'A*03:29', 'A*03:30', 'A*03:31', 'A*03:32', 'A*03:33', 'A*03:34',
                 'A*03:35', 'A*03:37', 'A*03:38', 'A*03:39', 'A*03:40', 'A*03:41', 'A*03:42', 'A*03:43', 'A*03:44',
                 'A*03:45', 'A*03:46', 'A*03:47', 'A*03:48', 'A*03:49', 'A*03:50', 'A*03:51', 'A*03:52', 'A*03:53',
                 'A*03:54', 'A*03:55', 'A*03:56', 'A*03:57', 'A*03:58', 'A*03:59', 'A*03:60', 'A*03:61', 'A*03:62',
                 'A*03:63', 'A*03:64', 'A*03:65', 'A*03:66', 'A*03:67', 'A*03:70', 'A*03:71', 'A*03:72', 'A*03:73',
                 'A*03:74', 'A*03:75', 'A*03:76', 'A*03:77', 'A*03:78', 'A*03:79', 'A*03:80', 'A*03:81', 'A*03:82',
                 'A*11:01', 'A*11:02', 'A*11:03', 'A*11:04', 'A*11:05', 'A*11:06', 'A*11:07', 'A*11:08', 'A*11:09',
                 'A*11:10', 'A*11:11', 'A*11:12', 'A*11:13', 'A*11:14', 'A*11:15', 'A*11:16', 'A*11:17', 'A*11:18',
                 'A*11:19', 'A*11:20', 'A*11:22', 'A*11:23', 'A*11:24', 'A*11:25', 'A*11:26', 'A*11:27', 'A*11:29',
                 'A*11:30', 'A*11:31', 'A*11:32', 'A*11:33', 'A*11:34', 'A*11:35', 'A*11:36', 'A*11:37', 'A*11:38',
                 'A*11:39', 'A*11:40', 'A*11:41', 'A*11:42', 'A*11:43', 'A*11:44', 'A*11:45', 'A*11:46', 'A*11:47',
                 'A*11:48', 'A*11:49', 'A*11:51', 'A*11:53', 'A*11:54', 'A*11:55', 'A*11:56', 'A*11:57', 'A*11:58',
                 'A*11:59', 'A*11:60', 'A*11:61', 'A*11:62', 'A*11:63', 'A*11:64', 'A*23:01', 'A*23:02', 'A*23:03',
                 'A*23:04', 'A*23:05', 'A*23:06', 'A*23:09', 'A*23:10', 'A*23:12', 'A*23:13', 'A*23:14', 'A*23:15',
                 'A*23:16', 'A*23:17', 'A*23:18', 'A*23:20', 'A*23:21', 'A*23:22', 'A*23:23', 'A*23:24', 'A*23:25',
                 'A*23:26', 'A*24:02', 'A*24:03', 'A*24:04', 'A*24:05', 'A*24:06', 'A*24:07', 'A*24:08', 'A*24:10',
                 'A*24:100', 'A*24:101', 'A*24:102', 'A*24:103', 'A*24:104', 'A*24:105', 'A*24:106', 'A*24:107',
                 'A*24:108', 'A*24:109', 'A*24:110', 'A*24:111', 'A*24:112', 'A*24:113', 'A*24:114', 'A*24:115',
                 'A*24:116', 'A*24:117', 'A*24:118', 'A*24:119', 'A*24:120', 'A*24:121', 'A*24:122', 'A*24:123',
                 'A*24:124', 'A*24:125', 'A*24:126', 'A*24:127', 'A*24:128', 'A*24:129', 'A*24:13', 'A*24:130',
                 'A*24:131', 'A*24:133', 'A*24:134', 'A*24:135', 'A*24:136', 'A*24:137', 'A*24:138', 'A*24:139',
                 'A*24:14', 'A*24:140', 'A*24:141', 'A*24:142', 'A*24:143', 'A*24:144', 'A*24:15', 'A*24:17', 'A*24:18',
                 'A*24:19', 'A*24:20', 'A*24:21', 'A*24:22', 'A*24:23', 'A*24:24', 'A*24:25', 'A*24:26', 'A*24:27',
                 'A*24:28', 'A*24:29', 'A*24:30', 'A*24:31', 'A*24:32', 'A*24:33', 'A*24:34', 'A*24:35', 'A*24:37',
                 'A*24:38', 'A*24:39', 'A*24:41', 'A*24:42', 'A*24:43', 'A*24:44', 'A*24:46', 'A*24:47', 'A*24:49',
                 'A*24:50', 'A*24:51', 'A*24:52', 'A*24:53', 'A*24:54', 'A*24:55', 'A*24:56', 'A*24:57', 'A*24:58',
                 'A*24:59', 'A*24:61', 'A*24:62', 'A*24:63', 'A*24:64', 'A*24:66', 'A*24:67', 'A*24:68', 'A*24:69',
                 'A*24:70', 'A*24:71', 'A*24:72', 'A*24:73', 'A*24:74', 'A*24:75', 'A*24:76', 'A*24:77', 'A*24:78',
                 'A*24:79', 'A*24:80', 'A*24:81', 'A*24:82', 'A*24:85', 'A*24:87', 'A*24:88', 'A*24:89', 'A*24:91',
                 'A*24:92', 'A*24:93', 'A*24:94', 'A*24:95', 'A*24:96', 'A*24:97', 'A*24:98', 'A*24:99', 'A*25:01',
                 'A*25:02', 'A*25:03', 'A*25:04', 'A*25:05', 'A*25:06', 'A*25:07', 'A*25:08', 'A*25:09', 'A*25:10',
                 'A*25:11', 'A*25:13', 'A*26:01', 'A*26:02', 'A*26:03', 'A*26:04', 'A*26:05', 'A*26:06', 'A*26:07',
                 'A*26:08', 'A*26:09', 'A*26:10', 'A*26:12', 'A*26:13', 'A*26:14', 'A*26:15', 'A*26:16', 'A*26:17',
                 'A*26:18', 'A*26:19', 'A*26:20', 'A*26:21', 'A*26:22', 'A*26:23', 'A*26:24', 'A*26:26', 'A*26:27',
                 'A*26:28', 'A*26:29', 'A*26:30', 'A*26:31', 'A*26:32', 'A*26:33', 'A*26:34', 'A*26:35', 'A*26:36',
                 'A*26:37', 'A*26:38', 'A*26:39', 'A*26:40', 'A*26:41', 'A*26:42', 'A*26:43', 'A*26:45', 'A*26:46',
                 'A*26:47', 'A*26:48', 'A*26:49', 'A*26:50', 'A*29:01', 'A*29:02', 'A*29:03', 'A*29:04', 'A*29:05',
                 'A*29:06', 'A*29:07', 'A*29:09', 'A*29:10', 'A*29:11', 'A*29:12', 'A*29:13', 'A*29:14', 'A*29:15',
                 'A*29:16', 'A*29:17', 'A*29:18', 'A*29:19', 'A*29:20', 'A*29:21', 'A*29:22', 'A*30:01', 'A*30:02',
                 'A*30:03', 'A*30:04', 'A*30:06', 'A*30:07', 'A*30:08', 'A*30:09', 'A*30:10', 'A*30:11', 'A*30:12',
                 'A*30:13', 'A*30:15', 'A*30:16', 'A*30:17', 'A*30:18', 'A*30:19', 'A*30:20', 'A*30:22', 'A*30:23',
                 'A*30:24', 'A*30:25', 'A*30:26', 'A*30:28', 'A*30:29', 'A*30:30', 'A*30:31', 'A*30:32', 'A*30:33',
                 'A*30:34', 'A*30:35', 'A*30:36', 'A*30:37', 'A*30:38', 'A*30:39', 'A*30:40', 'A*30:41', 'A*31:01',
                 'A*31:02', 'A*31:03', 'A*31:04', 'A*31:05', 'A*31:06', 'A*31:07', 'A*31:08', 'A*31:09', 'A*31:10',
                 'A*31:11', 'A*31:12', 'A*31:13', 'A*31:15', 'A*31:16', 'A*31:17', 'A*31:18', 'A*31:19', 'A*31:20',
                 'A*31:21', 'A*31:22', 'A*31:23', 'A*31:24', 'A*31:25', 'A*31:26', 'A*31:27', 'A*31:28', 'A*31:29',
                 'A*31:30', 'A*31:31', 'A*31:32', 'A*31:33', 'A*31:34', 'A*31:35', 'A*31:36', 'A*31:37', 'A*32:01',
                 'A*32:02', 'A*32:03', 'A*32:04', 'A*32:05', 'A*32:06', 'A*32:07', 'A*32:08', 'A*32:09', 'A*32:10',
                 'A*32:12', 'A*32:13', 'A*32:14', 'A*32:15', 'A*32:16', 'A*32:17', 'A*32:18', 'A*32:20', 'A*32:21',
                 'A*32:22', 'A*32:23', 'A*32:24', 'A*32:25', 'A*33:01', 'A*33:03', 'A*33:04', 'A*33:05', 'A*33:06',
                 'A*33:07', 'A*33:08', 'A*33:09', 'A*33:10', 'A*33:11', 'A*33:12', 'A*33:13', 'A*33:14', 'A*33:15',
                 'A*33:16', 'A*33:17', 'A*33:18', 'A*33:19', 'A*33:20', 'A*33:21', 'A*33:22', 'A*33:23', 'A*33:24',
                 'A*33:25', 'A*33:26', 'A*33:27', 'A*33:28', 'A*33:29', 'A*33:30', 'A*33:31', 'A*34:01', 'A*34:02',
                 'A*34:03', 'A*34:04', 'A*34:05', 'A*34:06', 'A*34:07', 'A*34:08', 'A*36:01', 'A*36:02', 'A*36:03',
                 'A*36:04', 'A*36:05', 'A*43:01', 'A*66:01', 'A*66:02', 'A*66:03', 'A*66:04', 'A*66:05', 'A*66:06',
                 'A*66:07', 'A*66:08', 'A*66:09', 'A*66:10', 'A*66:11', 'A*66:12', 'A*66:13', 'A*66:14', 'A*66:15',
                 'A*68:01', 'A*68:02', 'A*68:03', 'A*68:04', 'A*68:05', 'A*68:06', 'A*68:07', 'A*68:08', 'A*68:09',
                 'A*68:10', 'A*68:12', 'A*68:13', 'A*68:14', 'A*68:15', 'A*68:16', 'A*68:17', 'A*68:19', 'A*68:20',
                 'A*68:21', 'A*68:22', 'A*68:23', 'A*68:24', 'A*68:25', 'A*68:26', 'A*68:27', 'A*68:28', 'A*68:29',
                 'A*68:30', 'A*68:31', 'A*68:32', 'A*68:33', 'A*68:34', 'A*68:35', 'A*68:36', 'A*68:37', 'A*68:38',
                 'A*68:39', 'A*68:40', 'A*68:41', 'A*68:42', 'A*68:43', 'A*68:44', 'A*68:45', 'A*68:46', 'A*68:47',
                 'A*68:48', 'A*68:50', 'A*68:51', 'A*68:52', 'A*68:53', 'A*68:54', 'A*69:01', 'A*74:01', 'A*74:02',
                 'A*74:03', 'A*74:04', 'A*74:05', 'A*74:06', 'A*74:07', 'A*74:08', 'A*74:09', 'A*74:10', 'A*74:11',
                 'A*74:13', 'A*80:01', 'A*80:02', 'B*07:02', 'B*07:03', 'B*07:04', 'B*07:05', 'B*07:06', 'B*07:07',
                 'B*07:08', 'B*07:09', 'B*07:10', 'B*07:100', 'B*07:101', 'B*07:102', 'B*07:103', 'B*07:104',
                 'B*07:105', 'B*07:106', 'B*07:107', 'B*07:108', 'B*07:109', 'B*07:11', 'B*07:110', 'B*07:112',
                 'B*07:113', 'B*07:114', 'B*07:115', 'B*07:12', 'B*07:13', 'B*07:14', 'B*07:15', 'B*07:16', 'B*07:17',
                 'B*07:18', 'B*07:19', 'B*07:20', 'B*07:21', 'B*07:22', 'B*07:23', 'B*07:24', 'B*07:25', 'B*07:26',
                 'B*07:27', 'B*07:28', 'B*07:29', 'B*07:30', 'B*07:31', 'B*07:32', 'B*07:33', 'B*07:34', 'B*07:35',
                 'B*07:36', 'B*07:37', 'B*07:38', 'B*07:39', 'B*07:40', 'B*07:41', 'B*07:42', 'B*07:43', 'B*07:44',
                 'B*07:45', 'B*07:46', 'B*07:47', 'B*07:48', 'B*07:50', 'B*07:51', 'B*07:52', 'B*07:53', 'B*07:54',
                 'B*07:55', 'B*07:56', 'B*07:57', 'B*07:58', 'B*07:59', 'B*07:60', 'B*07:61', 'B*07:62', 'B*07:63',
                 'B*07:64', 'B*07:65', 'B*07:66', 'B*07:68', 'B*07:69', 'B*07:70', 'B*07:71', 'B*07:72', 'B*07:73',
                 'B*07:74', 'B*07:75', 'B*07:76', 'B*07:77', 'B*07:78', 'B*07:79', 'B*07:80', 'B*07:81', 'B*07:82',
                 'B*07:83', 'B*07:84', 'B*07:85', 'B*07:86', 'B*07:87', 'B*07:88', 'B*07:89', 'B*07:90', 'B*07:91',
                 'B*07:92', 'B*07:93', 'B*07:94', 'B*07:95', 'B*07:96', 'B*07:97', 'B*07:98', 'B*07:99', 'B*08:01',
                 'B*08:02', 'B*08:03', 'B*08:04', 'B*08:05', 'B*08:07', 'B*08:09', 'B*08:10', 'B*08:11', 'B*08:12',
                 'B*08:13', 'B*08:14', 'B*08:15', 'B*08:16', 'B*08:17', 'B*08:18', 'B*08:20', 'B*08:21', 'B*08:22',
                 'B*08:23', 'B*08:24', 'B*08:25', 'B*08:26', 'B*08:27', 'B*08:28', 'B*08:29', 'B*08:31', 'B*08:32',
                 'B*08:33', 'B*08:34', 'B*08:35', 'B*08:36', 'B*08:37', 'B*08:38', 'B*08:39', 'B*08:40', 'B*08:41',
                 'B*08:42', 'B*08:43', 'B*08:44', 'B*08:45', 'B*08:46', 'B*08:47', 'B*08:48', 'B*08:49', 'B*08:50',
                 'B*08:51', 'B*08:52', 'B*08:53', 'B*08:54', 'B*08:55', 'B*08:56', 'B*08:57', 'B*08:58', 'B*08:59',
                 'B*08:60', 'B*08:61', 'B*08:62', 'B*13:01', 'B*13:02', 'B*13:03', 'B*13:04', 'B*13:06', 'B*13:09',
                 'B*13:10', 'B*13:11', 'B*13:12', 'B*13:13', 'B*13:14', 'B*13:15', 'B*13:16', 'B*13:17', 'B*13:18',
                 'B*13:19', 'B*13:20', 'B*13:21', 'B*13:22', 'B*13:23', 'B*13:25', 'B*13:26', 'B*13:27', 'B*13:28',
                 'B*13:29', 'B*13:30', 'B*13:31', 'B*13:32', 'B*13:33', 'B*13:34', 'B*13:35', 'B*13:36', 'B*13:37',
                 'B*13:38', 'B*13:39', 'B*14:01', 'B*14:02', 'B*14:03', 'B*14:04', 'B*14:05', 'B*14:06', 'B*14:08',
                 'B*14:09', 'B*14:10', 'B*14:11', 'B*14:12', 'B*14:13', 'B*14:14', 'B*14:15', 'B*14:16', 'B*14:17',
                 'B*14:18', 'B*15:01', 'B*15:02', 'B*15:03', 'B*15:04', 'B*15:05', 'B*15:06', 'B*15:07', 'B*15:08',
                 'B*15:09', 'B*15:10', 'B*15:101', 'B*15:102', 'B*15:103', 'B*15:104', 'B*15:105', 'B*15:106',
                 'B*15:107', 'B*15:108', 'B*15:109', 'B*15:11', 'B*15:110', 'B*15:112', 'B*15:113', 'B*15:114',
                 'B*15:115', 'B*15:116', 'B*15:117', 'B*15:118', 'B*15:119', 'B*15:12', 'B*15:120', 'B*15:121',
                 'B*15:122', 'B*15:123', 'B*15:124', 'B*15:125', 'B*15:126', 'B*15:127', 'B*15:128', 'B*15:129',
                 'B*15:13', 'B*15:131', 'B*15:132', 'B*15:133', 'B*15:134', 'B*15:135', 'B*15:136', 'B*15:137',
                 'B*15:138', 'B*15:139', 'B*15:14', 'B*15:140', 'B*15:141', 'B*15:142', 'B*15:143', 'B*15:144',
                 'B*15:145', 'B*15:146', 'B*15:147', 'B*15:148', 'B*15:15', 'B*15:150', 'B*15:151', 'B*15:152',
                 'B*15:153', 'B*15:154', 'B*15:155', 'B*15:156', 'B*15:157', 'B*15:158', 'B*15:159', 'B*15:16',
                 'B*15:160', 'B*15:161', 'B*15:162', 'B*15:163', 'B*15:164', 'B*15:165', 'B*15:166', 'B*15:167',
                 'B*15:168', 'B*15:169', 'B*15:17', 'B*15:170', 'B*15:171', 'B*15:172', 'B*15:173', 'B*15:174',
                 'B*15:175', 'B*15:176', 'B*15:177', 'B*15:178', 'B*15:179', 'B*15:18', 'B*15:180', 'B*15:183',
                 'B*15:184', 'B*15:185', 'B*15:186', 'B*15:187', 'B*15:188', 'B*15:189', 'B*15:19', 'B*15:191',
                 'B*15:192', 'B*15:193', 'B*15:194', 'B*15:195', 'B*15:196', 'B*15:197', 'B*15:198', 'B*15:199',
                 'B*15:20', 'B*15:200', 'B*15:201', 'B*15:202', 'B*15:21', 'B*15:23', 'B*15:24', 'B*15:25', 'B*15:27',
                 'B*15:28', 'B*15:29', 'B*15:30', 'B*15:31', 'B*15:32', 'B*15:33', 'B*15:34', 'B*15:35', 'B*15:36',
                 'B*15:37', 'B*15:38', 'B*15:39', 'B*15:40', 'B*15:42', 'B*15:43', 'B*15:44', 'B*15:45', 'B*15:46',
                 'B*15:47', 'B*15:48', 'B*15:49', 'B*15:50', 'B*15:51', 'B*15:52', 'B*15:53', 'B*15:54', 'B*15:55',
                 'B*15:56', 'B*15:57', 'B*15:58', 'B*15:60', 'B*15:61', 'B*15:62', 'B*15:63', 'B*15:64', 'B*15:65',
                 'B*15:66', 'B*15:67', 'B*15:68', 'B*15:69', 'B*15:70', 'B*15:71', 'B*15:72', 'B*15:73', 'B*15:74',
                 'B*15:75', 'B*15:76', 'B*15:77', 'B*15:78', 'B*15:80', 'B*15:81', 'B*15:82', 'B*15:83', 'B*15:84',
                 'B*15:85', 'B*15:86', 'B*15:87', 'B*15:88', 'B*15:89', 'B*15:90', 'B*15:91', 'B*15:92', 'B*15:93',
                 'B*15:95', 'B*15:96', 'B*15:97', 'B*15:98', 'B*15:99', 'B*18:01', 'B*18:02', 'B*18:03', 'B*18:04',
                 'B*18:05', 'B*18:06', 'B*18:07', 'B*18:08', 'B*18:09', 'B*18:10', 'B*18:11', 'B*18:12', 'B*18:13',
                 'B*18:14', 'B*18:15', 'B*18:18', 'B*18:19', 'B*18:20', 'B*18:21', 'B*18:22', 'B*18:24', 'B*18:25',
                 'B*18:26', 'B*18:27', 'B*18:28', 'B*18:29', 'B*18:30', 'B*18:31', 'B*18:32', 'B*18:33', 'B*18:34',
                 'B*18:35', 'B*18:36', 'B*18:37', 'B*18:38', 'B*18:39', 'B*18:40', 'B*18:41', 'B*18:42', 'B*18:43',
                 'B*18:44', 'B*18:45', 'B*18:46', 'B*18:47', 'B*18:48', 'B*18:49', 'B*18:50', 'B*27:01', 'B*27:02',
                 'B*27:03', 'B*27:04', 'B*27:05', 'B*27:06', 'B*27:07', 'B*27:08', 'B*27:09', 'B*27:10', 'B*27:11',
                 'B*27:12', 'B*27:13', 'B*27:14', 'B*27:15', 'B*27:16', 'B*27:17', 'B*27:18', 'B*27:19', 'B*27:20',
                 'B*27:21', 'B*27:23', 'B*27:24', 'B*27:25', 'B*27:26', 'B*27:27', 'B*27:28', 'B*27:29', 'B*27:30',
                 'B*27:31', 'B*27:32', 'B*27:33', 'B*27:34', 'B*27:35', 'B*27:36', 'B*27:37', 'B*27:38', 'B*27:39',
                 'B*27:40', 'B*27:41', 'B*27:42', 'B*27:43', 'B*27:44', 'B*27:45', 'B*27:46', 'B*27:47', 'B*27:48',
                 'B*27:49', 'B*27:50', 'B*27:51', 'B*27:52', 'B*27:53', 'B*27:54', 'B*27:55', 'B*27:56', 'B*27:57',
                 'B*27:58', 'B*27:60', 'B*27:61', 'B*27:62', 'B*27:63', 'B*27:67', 'B*27:68', 'B*27:69', 'B*35:01',
                 'B*35:02', 'B*35:03', 'B*35:04', 'B*35:05', 'B*35:06', 'B*35:07', 'B*35:08', 'B*35:09', 'B*35:10',
                 'B*35:100', 'B*35:101', 'B*35:102', 'B*35:103', 'B*35:104', 'B*35:105', 'B*35:106', 'B*35:107',
                 'B*35:108', 'B*35:109', 'B*35:11', 'B*35:110', 'B*35:111', 'B*35:112', 'B*35:113', 'B*35:114',
                 'B*35:115', 'B*35:116', 'B*35:117', 'B*35:118', 'B*35:119', 'B*35:12', 'B*35:120', 'B*35:121',
                 'B*35:122', 'B*35:123', 'B*35:124', 'B*35:125', 'B*35:126', 'B*35:127', 'B*35:128', 'B*35:13',
                 'B*35:131', 'B*35:132', 'B*35:133', 'B*35:135', 'B*35:136', 'B*35:137', 'B*35:138', 'B*35:139',
                 'B*35:14', 'B*35:140', 'B*35:141', 'B*35:142', 'B*35:143', 'B*35:144', 'B*35:15', 'B*35:16', 'B*35:17',
                 'B*35:18', 'B*35:19', 'B*35:20', 'B*35:21', 'B*35:22', 'B*35:23', 'B*35:24', 'B*35:25', 'B*35:26',
                 'B*35:27', 'B*35:28', 'B*35:29', 'B*35:30', 'B*35:31', 'B*35:32', 'B*35:33', 'B*35:34', 'B*35:35',
                 'B*35:36', 'B*35:37', 'B*35:38', 'B*35:39', 'B*35:41', 'B*35:42', 'B*35:43', 'B*35:44', 'B*35:45',
                 'B*35:46', 'B*35:47', 'B*35:48', 'B*35:49', 'B*35:50', 'B*35:51', 'B*35:52', 'B*35:54', 'B*35:55',
                 'B*35:56', 'B*35:57', 'B*35:58', 'B*35:59', 'B*35:60', 'B*35:61', 'B*35:62', 'B*35:63', 'B*35:64',
                 'B*35:66', 'B*35:67', 'B*35:68', 'B*35:69', 'B*35:70', 'B*35:71', 'B*35:72', 'B*35:74', 'B*35:75',
                 'B*35:76', 'B*35:77', 'B*35:78', 'B*35:79', 'B*35:80', 'B*35:81', 'B*35:82', 'B*35:83', 'B*35:84',
                 'B*35:85', 'B*35:86', 'B*35:87', 'B*35:88', 'B*35:89', 'B*35:90', 'B*35:91', 'B*35:92', 'B*35:93',
                 'B*35:94', 'B*35:95', 'B*35:96', 'B*35:97', 'B*35:98', 'B*35:99', 'B*37:01', 'B*37:02', 'B*37:04',
                 'B*37:05', 'B*37:06', 'B*37:07', 'B*37:08', 'B*37:09', 'B*37:10', 'B*37:11', 'B*37:12', 'B*37:13',
                 'B*37:14', 'B*37:15', 'B*37:17', 'B*37:18', 'B*37:19', 'B*37:20', 'B*37:21', 'B*37:22', 'B*37:23',
                 'B*38:01', 'B*38:02', 'B*38:03', 'B*38:04', 'B*38:05', 'B*38:06', 'B*38:07', 'B*38:08', 'B*38:09',
                 'B*38:10', 'B*38:11', 'B*38:12', 'B*38:13', 'B*38:14', 'B*38:15', 'B*38:16', 'B*38:17', 'B*38:18',
                 'B*38:19', 'B*38:20', 'B*38:21', 'B*38:22', 'B*38:23', 'B*39:01', 'B*39:02', 'B*39:03', 'B*39:04',
                 'B*39:05', 'B*39:06', 'B*39:07', 'B*39:08', 'B*39:09', 'B*39:10', 'B*39:11', 'B*39:12', 'B*39:13',
                 'B*39:14', 'B*39:15', 'B*39:16', 'B*39:17', 'B*39:18', 'B*39:19', 'B*39:20', 'B*39:22', 'B*39:23',
                 'B*39:24', 'B*39:26', 'B*39:27', 'B*39:28', 'B*39:29', 'B*39:30', 'B*39:31', 'B*39:32', 'B*39:33',
                 'B*39:34', 'B*39:35', 'B*39:36', 'B*39:37', 'B*39:39', 'B*39:41', 'B*39:42', 'B*39:43', 'B*39:44',
                 'B*39:45', 'B*39:46', 'B*39:47', 'B*39:48', 'B*39:49', 'B*39:50', 'B*39:51', 'B*39:52', 'B*39:53',
                 'B*39:54', 'B*39:55', 'B*39:56', 'B*39:57', 'B*39:58', 'B*39:59', 'B*39:60', 'B*40:01', 'B*40:02',
                 'B*40:03', 'B*40:04', 'B*40:05', 'B*40:06', 'B*40:07', 'B*40:08', 'B*40:09', 'B*40:10', 'B*40:100',
                 'B*40:101', 'B*40:102', 'B*40:103', 'B*40:104', 'B*40:105', 'B*40:106', 'B*40:107', 'B*40:108',
                 'B*40:109', 'B*40:11', 'B*40:110', 'B*40:111', 'B*40:112', 'B*40:113', 'B*40:114', 'B*40:115',
                 'B*40:116', 'B*40:117', 'B*40:119', 'B*40:12', 'B*40:120', 'B*40:121', 'B*40:122', 'B*40:123',
                 'B*40:124', 'B*40:125', 'B*40:126', 'B*40:127', 'B*40:128', 'B*40:129', 'B*40:13', 'B*40:130',
                 'B*40:131', 'B*40:132', 'B*40:134', 'B*40:135', 'B*40:136', 'B*40:137', 'B*40:138', 'B*40:139',
                 'B*40:14', 'B*40:140', 'B*40:141', 'B*40:143', 'B*40:145', 'B*40:146', 'B*40:147', 'B*40:15',
                 'B*40:16', 'B*40:18', 'B*40:19', 'B*40:20', 'B*40:21', 'B*40:23', 'B*40:24', 'B*40:25', 'B*40:26',
                 'B*40:27', 'B*40:28', 'B*40:29', 'B*40:30', 'B*40:31', 'B*40:32', 'B*40:33', 'B*40:34', 'B*40:35',
                 'B*40:36', 'B*40:37', 'B*40:38', 'B*40:39', 'B*40:40', 'B*40:42', 'B*40:43', 'B*40:44', 'B*40:45',
                 'B*40:46', 'B*40:47', 'B*40:48', 'B*40:49', 'B*40:50', 'B*40:51', 'B*40:52', 'B*40:53', 'B*40:54',
                 'B*40:55', 'B*40:56', 'B*40:57', 'B*40:58', 'B*40:59', 'B*40:60', 'B*40:61', 'B*40:62', 'B*40:63',
                 'B*40:64', 'B*40:65', 'B*40:66', 'B*40:67', 'B*40:68', 'B*40:69', 'B*40:70', 'B*40:71', 'B*40:72',
                 'B*40:73', 'B*40:74', 'B*40:75', 'B*40:76', 'B*40:77', 'B*40:78', 'B*40:79', 'B*40:80', 'B*40:81',
                 'B*40:82', 'B*40:83', 'B*40:84', 'B*40:85', 'B*40:86', 'B*40:87', 'B*40:88', 'B*40:89', 'B*40:90',
                 'B*40:91', 'B*40:92', 'B*40:93', 'B*40:94', 'B*40:95', 'B*40:96', 'B*40:97', 'B*40:98', 'B*40:99',
                 'B*41:01', 'B*41:02', 'B*41:03', 'B*41:04', 'B*41:05', 'B*41:06', 'B*41:07', 'B*41:08', 'B*41:09',
                 'B*41:10', 'B*41:11', 'B*41:12', 'B*42:01', 'B*42:02', 'B*42:04', 'B*42:05', 'B*42:06', 'B*42:07',
                 'B*42:08', 'B*42:09', 'B*42:10', 'B*42:11', 'B*42:12', 'B*42:13', 'B*42:14', 'B*44:02', 'B*44:03',
                 'B*44:04', 'B*44:05', 'B*44:06', 'B*44:07', 'B*44:08', 'B*44:09', 'B*44:10', 'B*44:100', 'B*44:101',
                 'B*44:102', 'B*44:103', 'B*44:104', 'B*44:105', 'B*44:106', 'B*44:107', 'B*44:109', 'B*44:11',
                 'B*44:110', 'B*44:12', 'B*44:13', 'B*44:14', 'B*44:15', 'B*44:16', 'B*44:17', 'B*44:18', 'B*44:20',
                 'B*44:21', 'B*44:22', 'B*44:24', 'B*44:25', 'B*44:26', 'B*44:27', 'B*44:28', 'B*44:29', 'B*44:30',
                 'B*44:31', 'B*44:32', 'B*44:33', 'B*44:34', 'B*44:35', 'B*44:36', 'B*44:37', 'B*44:38', 'B*44:39',
                 'B*44:40', 'B*44:41', 'B*44:42', 'B*44:43', 'B*44:44', 'B*44:45', 'B*44:46', 'B*44:47', 'B*44:48',
                 'B*44:49', 'B*44:50', 'B*44:51', 'B*44:53', 'B*44:54', 'B*44:55', 'B*44:57', 'B*44:59', 'B*44:60',
                 'B*44:62', 'B*44:63', 'B*44:64', 'B*44:65', 'B*44:66', 'B*44:67', 'B*44:68', 'B*44:69', 'B*44:70',
                 'B*44:71', 'B*44:72', 'B*44:73', 'B*44:74', 'B*44:75', 'B*44:76', 'B*44:77', 'B*44:78', 'B*44:79',
                 'B*44:80', 'B*44:81', 'B*44:82', 'B*44:83', 'B*44:84', 'B*44:85', 'B*44:86', 'B*44:87', 'B*44:88',
                 'B*44:89', 'B*44:90', 'B*44:91', 'B*44:92', 'B*44:93', 'B*44:94', 'B*44:95', 'B*44:96', 'B*44:97',
                 'B*44:98', 'B*44:99', 'B*45:01', 'B*45:02', 'B*45:03', 'B*45:04', 'B*45:05', 'B*45:06', 'B*45:07',
                 'B*45:08', 'B*45:09', 'B*45:10', 'B*45:11', 'B*45:12', 'B*46:01', 'B*46:02', 'B*46:03', 'B*46:04',
                 'B*46:05', 'B*46:06', 'B*46:08', 'B*46:09', 'B*46:10', 'B*46:11', 'B*46:12', 'B*46:13', 'B*46:14',
                 'B*46:16', 'B*46:17', 'B*46:18', 'B*46:19', 'B*46:20', 'B*46:21', 'B*46:22', 'B*46:23', 'B*46:24',
                 'B*47:01', 'B*47:02', 'B*47:03', 'B*47:04', 'B*47:05', 'B*47:06', 'B*47:07', 'B*48:01', 'B*48:02',
                 'B*48:03', 'B*48:04', 'B*48:05', 'B*48:06', 'B*48:07', 'B*48:08', 'B*48:09', 'B*48:10', 'B*48:11',
                 'B*48:12', 'B*48:13', 'B*48:14', 'B*48:15', 'B*48:16', 'B*48:17', 'B*48:18', 'B*48:19', 'B*48:20',
                 'B*48:21', 'B*48:22', 'B*48:23', 'B*49:01', 'B*49:02', 'B*49:03', 'B*49:04', 'B*49:05', 'B*49:06',
                 'B*49:07', 'B*49:08', 'B*49:09', 'B*49:10', 'B*50:01', 'B*50:02', 'B*50:04', 'B*50:05', 'B*50:06',
                 'B*50:07', 'B*50:08', 'B*50:09', 'B*51:01', 'B*51:02', 'B*51:03', 'B*51:04', 'B*51:05', 'B*51:06',
                 'B*51:07', 'B*51:08', 'B*51:09', 'B*51:12', 'B*51:13', 'B*51:14', 'B*51:15', 'B*51:16', 'B*51:17',
                 'B*51:18', 'B*51:19', 'B*51:20', 'B*51:21', 'B*51:22', 'B*51:23', 'B*51:24', 'B*51:26', 'B*51:28',
                 'B*51:29', 'B*51:30', 'B*51:31', 'B*51:32', 'B*51:33', 'B*51:34', 'B*51:35', 'B*51:36', 'B*51:37',
                 'B*51:38', 'B*51:39', 'B*51:40', 'B*51:42', 'B*51:43', 'B*51:45', 'B*51:46', 'B*51:48', 'B*51:49',
                 'B*51:50', 'B*51:51', 'B*51:52', 'B*51:53', 'B*51:54', 'B*51:55', 'B*51:56', 'B*51:57', 'B*51:58',
                 'B*51:59', 'B*51:60', 'B*51:61', 'B*51:62', 'B*51:63', 'B*51:64', 'B*51:65', 'B*51:66', 'B*51:67',
                 'B*51:68', 'B*51:69', 'B*51:70', 'B*51:71', 'B*51:72', 'B*51:73', 'B*51:74', 'B*51:75', 'B*51:76',
                 'B*51:77', 'B*51:78', 'B*51:79', 'B*51:80', 'B*51:81', 'B*51:82', 'B*51:83', 'B*51:84', 'B*51:85',
                 'B*51:86', 'B*51:87', 'B*51:88', 'B*51:89', 'B*51:90', 'B*51:91', 'B*51:92', 'B*51:93', 'B*51:94',
                 'B*51:95', 'B*51:96', 'B*52:01', 'B*52:02', 'B*52:03', 'B*52:04', 'B*52:05', 'B*52:06', 'B*52:07',
                 'B*52:08', 'B*52:09', 'B*52:10', 'B*52:11', 'B*52:12', 'B*52:13', 'B*52:14', 'B*52:15', 'B*52:16',
                 'B*52:17', 'B*52:18', 'B*52:19', 'B*52:20', 'B*52:21', 'B*53:01', 'B*53:02', 'B*53:03', 'B*53:04',
                 'B*53:05', 'B*53:06', 'B*53:07', 'B*53:08', 'B*53:09', 'B*53:10', 'B*53:11', 'B*53:12', 'B*53:13',
                 'B*53:14', 'B*53:15', 'B*53:16', 'B*53:17', 'B*53:18', 'B*53:19', 'B*53:20', 'B*53:21', 'B*53:22',
                 'B*53:23', 'B*54:01', 'B*54:02', 'B*54:03', 'B*54:04', 'B*54:06', 'B*54:07', 'B*54:09', 'B*54:10',
                 'B*54:11', 'B*54:12', 'B*54:13', 'B*54:14', 'B*54:15', 'B*54:16', 'B*54:17', 'B*54:18', 'B*54:19',
                 'B*54:20', 'B*54:21', 'B*54:22', 'B*54:23', 'B*55:01', 'B*55:02', 'B*55:03', 'B*55:04', 'B*55:05',
                 'B*55:07', 'B*55:08', 'B*55:09', 'B*55:10', 'B*55:11', 'B*55:12', 'B*55:13', 'B*55:14', 'B*55:15',
                 'B*55:16', 'B*55:17', 'B*55:18', 'B*55:19', 'B*55:20', 'B*55:21', 'B*55:22', 'B*55:23', 'B*55:24',
                 'B*55:25', 'B*55:26', 'B*55:27', 'B*55:28', 'B*55:29', 'B*55:30', 'B*55:31', 'B*55:32', 'B*55:33',
                 'B*55:34', 'B*55:35', 'B*55:36', 'B*55:37', 'B*55:38', 'B*55:39', 'B*55:40', 'B*55:41', 'B*55:42',
                 'B*55:43', 'B*56:01', 'B*56:02', 'B*56:03', 'B*56:04', 'B*56:05', 'B*56:06', 'B*56:07', 'B*56:08',
                 'B*56:09', 'B*56:10', 'B*56:11', 'B*56:12', 'B*56:13', 'B*56:14', 'B*56:15', 'B*56:16', 'B*56:17',
                 'B*56:18', 'B*56:20', 'B*56:21', 'B*56:22', 'B*56:23', 'B*56:24', 'B*56:25', 'B*56:26', 'B*56:27',
                 'B*56:29', 'B*57:01', 'B*57:02', 'B*57:03', 'B*57:04', 'B*57:05', 'B*57:06', 'B*57:07', 'B*57:08',
                 'B*57:09', 'B*57:10', 'B*57:11', 'B*57:12', 'B*57:13', 'B*57:14', 'B*57:15', 'B*57:16', 'B*57:17',
                 'B*57:18', 'B*57:19', 'B*57:20', 'B*57:21', 'B*57:22', 'B*57:23', 'B*57:24', 'B*57:25', 'B*57:26',
                 'B*57:27', 'B*57:29', 'B*57:30', 'B*57:31', 'B*57:32', 'B*58:01', 'B*58:02', 'B*58:04', 'B*58:05',
                 'B*58:06', 'B*58:07', 'B*58:08', 'B*58:09', 'B*58:11', 'B*58:12', 'B*58:13', 'B*58:14', 'B*58:15',
                 'B*58:16', 'B*58:18', 'B*58:19', 'B*58:20', 'B*58:21', 'B*58:22', 'B*58:23', 'B*58:24', 'B*58:25',
                 'B*58:26', 'B*58:27', 'B*58:28', 'B*58:29', 'B*58:30', 'B*59:01', 'B*59:02', 'B*59:03', 'B*59:04',
                 'B*59:05', 'B*67:01', 'B*67:02', 'B*73:01', 'B*73:02', 'B*78:01', 'B*78:02', 'B*78:03', 'B*78:04',
                 'B*78:05', 'B*78:06', 'B*78:07', 'B*81:01', 'B*81:02', 'B*81:03', 'B*81:05', 'B*82:01', 'B*82:02',
                 'B*82:03', 'B*83:01', 'C*01:02', 'C*01:03', 'C*01:04', 'C*01:05', 'C*01:06', 'C*01:07', 'C*01:08',
                 'C*01:09', 'C*01:10', 'C*01:11', 'C*01:12', 'C*01:13', 'C*01:14', 'C*01:15', 'C*01:16', 'C*01:17',
                 'C*01:18', 'C*01:19', 'C*01:20', 'C*01:21', 'C*01:22', 'C*01:23', 'C*01:24', 'C*01:25', 'C*01:26',
                 'C*01:27', 'C*01:28', 'C*01:29', 'C*01:30', 'C*01:31', 'C*01:32', 'C*01:33', 'C*01:34', 'C*01:35',
                 'C*01:36', 'C*01:38', 'C*01:39', 'C*01:40', 'C*02:02', 'C*02:03', 'C*02:04', 'C*02:05', 'C*02:06',
                 'C*02:07', 'C*02:08', 'C*02:09', 'C*02:10', 'C*02:11', 'C*02:12', 'C*02:13', 'C*02:14', 'C*02:15',
                 'C*02:16', 'C*02:17', 'C*02:18', 'C*02:19', 'C*02:20', 'C*02:21', 'C*02:22', 'C*02:23', 'C*02:24',
                 'C*02:26', 'C*02:27', 'C*02:28', 'C*02:29', 'C*02:30', 'C*02:31', 'C*02:32', 'C*02:33', 'C*02:34',
                 'C*02:35', 'C*02:36', 'C*02:37', 'C*02:39', 'C*02:40', 'C*03:01', 'C*03:02', 'C*03:03', 'C*03:04',
                 'C*03:05', 'C*03:06', 'C*03:07', 'C*03:08', 'C*03:09', 'C*03:10', 'C*03:11', 'C*03:12', 'C*03:13',
                 'C*03:14', 'C*03:15', 'C*03:16', 'C*03:17', 'C*03:18', 'C*03:19', 'C*03:21', 'C*03:23', 'C*03:24',
                 'C*03:25', 'C*03:26', 'C*03:27', 'C*03:28', 'C*03:29', 'C*03:30', 'C*03:31', 'C*03:32', 'C*03:33',
                 'C*03:34', 'C*03:35', 'C*03:36', 'C*03:37', 'C*03:38', 'C*03:39', 'C*03:40', 'C*03:41', 'C*03:42',
                 'C*03:43', 'C*03:44', 'C*03:45', 'C*03:46', 'C*03:47', 'C*03:48', 'C*03:49', 'C*03:50', 'C*03:51',
                 'C*03:52', 'C*03:53', 'C*03:54', 'C*03:55', 'C*03:56', 'C*03:57', 'C*03:58', 'C*03:59', 'C*03:60',
                 'C*03:61', 'C*03:62', 'C*03:63', 'C*03:64', 'C*03:65', 'C*03:66', 'C*03:67', 'C*03:68', 'C*03:69',
                 'C*03:70', 'C*03:71', 'C*03:72', 'C*03:73', 'C*03:74', 'C*03:75', 'C*03:76', 'C*03:77', 'C*03:78',
                 'C*03:79', 'C*03:80', 'C*03:81', 'C*03:82', 'C*03:83', 'C*03:84', 'C*03:85', 'C*03:86', 'C*03:87',
                 'C*03:88', 'C*03:89', 'C*03:90', 'C*03:91', 'C*03:92', 'C*03:93', 'C*03:94', 'C*04:01', 'C*04:03',
                 'C*04:04', 'C*04:05', 'C*04:06', 'C*04:07', 'C*04:08', 'C*04:10', 'C*04:11', 'C*04:12', 'C*04:13',
                 'C*04:14', 'C*04:15', 'C*04:16', 'C*04:17', 'C*04:18', 'C*04:19', 'C*04:20', 'C*04:23', 'C*04:24',
                 'C*04:25', 'C*04:26', 'C*04:27', 'C*04:28', 'C*04:29', 'C*04:30', 'C*04:31', 'C*04:32', 'C*04:33',
                 'C*04:34', 'C*04:35', 'C*04:36', 'C*04:37', 'C*04:38', 'C*04:39', 'C*04:40', 'C*04:41', 'C*04:42',
                 'C*04:43', 'C*04:44', 'C*04:45', 'C*04:46', 'C*04:47', 'C*04:48', 'C*04:49', 'C*04:50', 'C*04:51',
                 'C*04:52', 'C*04:53', 'C*04:54', 'C*04:55', 'C*04:56', 'C*04:57', 'C*04:58', 'C*04:60', 'C*04:61',
                 'C*04:62', 'C*04:63', 'C*04:64', 'C*04:65', 'C*04:66', 'C*04:67', 'C*04:68', 'C*04:69', 'C*04:70',
                 'C*05:01', 'C*05:03', 'C*05:04', 'C*05:05', 'C*05:06', 'C*05:08', 'C*05:09', 'C*05:10', 'C*05:11',
                 'C*05:12', 'C*05:13', 'C*05:14', 'C*05:15', 'C*05:16', 'C*05:17', 'C*05:18', 'C*05:19', 'C*05:20',
                 'C*05:21', 'C*05:22', 'C*05:23', 'C*05:24', 'C*05:25', 'C*05:26', 'C*05:27', 'C*05:28', 'C*05:29',
                 'C*05:30', 'C*05:31', 'C*05:32', 'C*05:33', 'C*05:34', 'C*05:35', 'C*05:36', 'C*05:37', 'C*05:38',
                 'C*05:39', 'C*05:40', 'C*05:41', 'C*05:42', 'C*05:43', 'C*05:44', 'C*05:45', 'C*06:02', 'C*06:03',
                 'C*06:04', 'C*06:05', 'C*06:06', 'C*06:07', 'C*06:08', 'C*06:09', 'C*06:10', 'C*06:11', 'C*06:12',
                 'C*06:13', 'C*06:14', 'C*06:15', 'C*06:17', 'C*06:18', 'C*06:19', 'C*06:20', 'C*06:21', 'C*06:22',
                 'C*06:23', 'C*06:24', 'C*06:25', 'C*06:26', 'C*06:27', 'C*06:28', 'C*06:29', 'C*06:30', 'C*06:31',
                 'C*06:32', 'C*06:33', 'C*06:34', 'C*06:35', 'C*06:36', 'C*06:37', 'C*06:38', 'C*06:39', 'C*06:40',
                 'C*06:41', 'C*06:42', 'C*06:43', 'C*06:44', 'C*06:45', 'C*07:01', 'C*07:02', 'C*07:03', 'C*07:04',
                 'C*07:05', 'C*07:06', 'C*07:07', 'C*07:08', 'C*07:09', 'C*07:10', 'C*07:100', 'C*07:101', 'C*07:102',
                 'C*07:103', 'C*07:105', 'C*07:106', 'C*07:107', 'C*07:108', 'C*07:109', 'C*07:11', 'C*07:110',
                 'C*07:111', 'C*07:112', 'C*07:113', 'C*07:114', 'C*07:115', 'C*07:116', 'C*07:117', 'C*07:118',
                 'C*07:119', 'C*07:12', 'C*07:120', 'C*07:122', 'C*07:123', 'C*07:124', 'C*07:125', 'C*07:126',
                 'C*07:127', 'C*07:128', 'C*07:129', 'C*07:13', 'C*07:130', 'C*07:131', 'C*07:132', 'C*07:133',
                 'C*07:134', 'C*07:135', 'C*07:136', 'C*07:137', 'C*07:138', 'C*07:139', 'C*07:14', 'C*07:140',
                 'C*07:141', 'C*07:142', 'C*07:143', 'C*07:144', 'C*07:145', 'C*07:146', 'C*07:147', 'C*07:148',
                 'C*07:149', 'C*07:15', 'C*07:16', 'C*07:17', 'C*07:18', 'C*07:19', 'C*07:20', 'C*07:21', 'C*07:22',
                 'C*07:23', 'C*07:24', 'C*07:25', 'C*07:26', 'C*07:27', 'C*07:28', 'C*07:29', 'C*07:30', 'C*07:31',
                 'C*07:35', 'C*07:36', 'C*07:37', 'C*07:38', 'C*07:39', 'C*07:40', 'C*07:41', 'C*07:42', 'C*07:43',
                 'C*07:44', 'C*07:45', 'C*07:46', 'C*07:47', 'C*07:48', 'C*07:49', 'C*07:50', 'C*07:51', 'C*07:52',
                 'C*07:53', 'C*07:54', 'C*07:56', 'C*07:57', 'C*07:58', 'C*07:59', 'C*07:60', 'C*07:62', 'C*07:63',
                 'C*07:64', 'C*07:65', 'C*07:66', 'C*07:67', 'C*07:68', 'C*07:69', 'C*07:70', 'C*07:71', 'C*07:72',
                 'C*07:73', 'C*07:74', 'C*07:75', 'C*07:76', 'C*07:77', 'C*07:78', 'C*07:79', 'C*07:80', 'C*07:81',
                 'C*07:82', 'C*07:83', 'C*07:84', 'C*07:85', 'C*07:86', 'C*07:87', 'C*07:88', 'C*07:89', 'C*07:90',
                 'C*07:91', 'C*07:92', 'C*07:93', 'C*07:94', 'C*07:95', 'C*07:96', 'C*07:97', 'C*07:99', 'C*08:01',
                 'C*08:02', 'C*08:03', 'C*08:04', 'C*08:05', 'C*08:06', 'C*08:07', 'C*08:08', 'C*08:09', 'C*08:10',
                 'C*08:11', 'C*08:12', 'C*08:13', 'C*08:14', 'C*08:15', 'C*08:16', 'C*08:17', 'C*08:18', 'C*08:19',
                 'C*08:20', 'C*08:21', 'C*08:22', 'C*08:23', 'C*08:24', 'C*08:25', 'C*08:27', 'C*08:28', 'C*08:29',
                 'C*08:30', 'C*08:31', 'C*08:32', 'C*08:33', 'C*08:34', 'C*08:35', 'C*12:02', 'C*12:03', 'C*12:04',
                 'C*12:05', 'C*12:06', 'C*12:07', 'C*12:08', 'C*12:09', 'C*12:10', 'C*12:11', 'C*12:12', 'C*12:13',
                 'C*12:14', 'C*12:15', 'C*12:16', 'C*12:17', 'C*12:18', 'C*12:19', 'C*12:20', 'C*12:21', 'C*12:22',
                 'C*12:23', 'C*12:24', 'C*12:25', 'C*12:26', 'C*12:27', 'C*12:28', 'C*12:29', 'C*12:30', 'C*12:31',
                 'C*12:32', 'C*12:33', 'C*12:34', 'C*12:35', 'C*12:36', 'C*12:37', 'C*12:38', 'C*12:40', 'C*12:41',
                 'C*12:43', 'C*12:44', 'C*14:02', 'C*14:03', 'C*14:04', 'C*14:05', 'C*14:06', 'C*14:08', 'C*14:09',
                 'C*14:10', 'C*14:11', 'C*14:12', 'C*14:13', 'C*14:14', 'C*14:15', 'C*14:16', 'C*14:17', 'C*14:18',
                 'C*14:19', 'C*14:20', 'C*15:02', 'C*15:03', 'C*15:04', 'C*15:05', 'C*15:06', 'C*15:07', 'C*15:08',
                 'C*15:09', 'C*15:10', 'C*15:11', 'C*15:12', 'C*15:13', 'C*15:15', 'C*15:16', 'C*15:17', 'C*15:18',
                 'C*15:19', 'C*15:20', 'C*15:21', 'C*15:22', 'C*15:23', 'C*15:24', 'C*15:25', 'C*15:26', 'C*15:27',
                 'C*15:28', 'C*15:29', 'C*15:30', 'C*15:31', 'C*15:33', 'C*15:34', 'C*15:35', 'C*16:01', 'C*16:02',
                 'C*16:04', 'C*16:06', 'C*16:07', 'C*16:08', 'C*16:09', 'C*16:10', 'C*16:11', 'C*16:12', 'C*16:13',
                 'C*16:14', 'C*16:15', 'C*16:17', 'C*16:18', 'C*16:19', 'C*16:20', 'C*16:21', 'C*16:22', 'C*16:23',
                 'C*16:24', 'C*16:25', 'C*16:26', 'C*17:01', 'C*17:02', 'C*17:03', 'C*17:04', 'C*17:05', 'C*17:06',
                 'C*17:07', 'C*18:01', 'C*18:02', 'C*18:03', 'E*01:01', 'G*01:01', 'G*01:02', 'G*01:03', 'G*01:04',
                 'G*01:06', 'G*01:07', 'G*01:08', 'G*01:09'])
    __version = "1.1"

    @property
    def version(self):
        return self.__version

    def convert_alleles(self, alleles):
        return ["HLA-%s%s:%s"%(a.locus, a.supertype, a.subtype) for a in alleles]

    @property
    def supportedAlleles(self):
        return self.__alleles

    @property
    def name(self):
        return self.__name

    @property
    def command(self):
        return self.__command

    @property
    def supportedLength(self):
        return self.__supported_length

    def parse_external_result(self, _file):
        result = defaultdict(defaultdict)
        with open(_file, "r") as f:
            for l in f:
                if l.startswith("#") or l.startswith("-") or l.strip() == "":
                    continue
                row = l.strip().split()
                if not row[0].isdigit():
                    continue

                epitope, allele, comb_score = row[3], row[2], row[7]
                result[allele.replace("*", "")][epitope] = float(comb_score)
        return result

    def get_external_version(self, path=None):
        #Undertermined pickpocket does not support --version or something similar
        return None

    def prepare_input(self, _input, _file):
        _file.write("\n".join(">pepe_%i\n%s"%(i, p) for i, p in enumerate(_input)))
