
from abc import ABC, abstractmethod
import re
import os
import logging
import subprocess

from nodes.helper import FileOutputNode
from utils import file_utils
from utils import signal_processing as sp
from utils.shell_run import shell_run
from config import OPENSMILE_HOME, KALDI_HOME

class Mp3ToWav(FileOutputNode):
    def run(self, mp3_file):
        self.log(logging.INFO, "Starting %s" % (mp3_file))

        if not mp3_file.endswith(".mp3"):
            self.log(logging.ERROR,"Failed %s. Not mp3 file" % (mp3_file))
            return

        wav_file = self.derive_new_file_path(mp3_file, "wav")

        if file_utils.should_run(mp3_file, wav_file):
            res = shell_run(["lame", "--decode", mp3_file, wav_file])

            if res != 0:
                self.log(logging.ERROR,"Failed %s -> %s with lame error code %i" % (mp3_file, wav_file, res))
                return

            self.log(logging.INFO, "Done %s -> %s" % (mp3_file, wav_file))

        self.emit(wav_file)


class ResampleWav(FileOutputNode):
    def setup(self, new_sr):
        self.new_sr = new_sr

    def run(self, wav_file):
        self.log(logging.INFO, "Starting %s" % (wav_file))

        if not wav_file.endswith(".wav"):
            self.log(logging.ERROR,"Failed %s. Not wav file" % (wav_file))
            return

        new_wav_file = self.derive_new_file_path(wav_file, "wav")

        if file_utils.should_run(wav_file, new_wav_file):
            res = shell_run(["sox", wav_file, "--rate", str(self.new_sr), new_wav_file])

            if res != 0:
                self.log(logging.ERROR,"Failed %s -> %s with lame error code %i" % (wav_file, new_wav_file, res))
                return

            self.log(logging.INFO, "Done %s -> %s" % (wav_file, new_wav_file))

        self.emit(new_wav_file)


class ShellCommand(FileOutputNode):
    """
        Take as input a format string representing a shell command that can accept an in_file and out_file.
        For example "someCommand -i {in_file} -o {out_file}"
        ext: Extension of the output file, ex. "wav", "csv"
    """
    def setup(self, command, ext):
        self.command = command
        self.ext = ext

    def run(self, in_file):
        self.log(logging.INFO, "Starting %s" % (in_file))

        out_file = self.derive_new_file_path(in_file, self.ext)

        if file_utils.should_run(in_file, out_file):
            cmd = self.command.format(in_file=in_file, out_file=out_file)
            res = shell_run(cmd.split(" "))

            if res != 0:
                self.log(logging.ERROR,"Failed %s -> %s with error code %i. cmd: %s" % (in_file, out_file, res, cmd))
                return

            self.log(logging.INFO, "Done %s -> %s" % (in_file, out_file))

        self.emit(out_file)


class OpenSmileRunner(FileOutputNode):
    """
        conf_file: Either absolute path to an opensmile conf file or the name of a config file in opensmile's config folder
        out_flag: Flag to use for the output file.
        extra_flags: A string of extra flags to pass to SMILExtract.
        out_ext: Extension of the output file
    """

    def setup(self, conf_file, out_flag="-csvoutput", extra_flags="-nologfile -noconsoleoutput -appendcsv 0", out_ext="csv"):
        self.conf_file = file_utils.locate_file(conf_file, [os.path.join(OPENSMILE_HOME, "config")])
        self.extra_flags = extra_flags.split(" ")
        self.out_flag = out_flag
        self.out_ext = out_ext

        opensmile_locations = [
            OPENSMILE_HOME,
            os.path.join(OPENSMILE_HOME, "bin"),
            os.path.join(OPENSMILE_HOME, "bin/linux_x64_standalone_static"),
        ]
        self.opensmile_exec = file_utils.locate_file("SMILExtract", opensmile_locations, use_path=True)


    def run(self, in_file):
        self.log(logging.INFO, "Starting %s" % (in_file))

        out_file = self.derive_new_file_path(in_file, self.out_ext)

        if file_utils.should_run(in_file, out_file):
            cmd = [self.opensmile_exec, "-C", self.conf_file, "-I", in_file, self.out_flag, out_file] + self.extra_flags
            res = shell_run(cmd)

            if res != 0:
                self.log(logging.ERROR,"Failed %s -> %s with SmileExtract error code %i. cmd: %s" % (in_file, out_file, res, " ".join(cmd)))
                return

            self.log(logging.INFO, "Done %s -> %s" % (in_file, out_file))

        self.emit([out_file])



class IS10_Paraling(OpenSmileRunner):

    def get_conf_name(self):
        return "IS10_paraling.conf"

    def get_command(self, wav_file, out_file):
        return [self.os_exec, "-C", self.conf_file, "-I", wav_file, "-csvoutput", out_file, "-nologfile", "-noconsoleoutput", "-appendcsv", "0"]


class IS10_Paraling_lld(OpenSmileRunner):

    def get_conf_name(self):
        return "IS10_paraling.conf"

    def get_command(self, wav_file, out_file):
        return [self.os_exec, "-C", self.conf_file, "-I", wav_file, "-lldcsvoutput", out_file, "-nologfile", "-noconsoleoutput", "-appendcsv", "0"]


class SplitSegments(FileOutputNode):
    """
       segment_mapping_fn is a pointer to a function that takes as input a file and sample rate and returns a
       list of all the segments in that file in the format [(start1, end1, segname1), (start2, end2, segname2), ...] where
       start and end are in given in samples. Each tuple in the list can also have a 4th item, which can be any string.
       This string will get saved in segname.txt

        This is useful for isolating events of interest in audio files. For example, if the segment mapping
        function returns a list of where all speech occurs in the input audio, this will isolate all occurrences of
        speech into individual files. The 4th item may contain the annotation of what was said in the segment.
    """
    def setup(self, segment_mapping_fn):
        self.segment_mapping_fn = segment_mapping_fn

    def run(self, in_file):
        self.log(logging.INFO, "Starting %s" % (in_file))

        if not in_file.endswith(".wav"):
            self.log(logging.ERROR, "Failed %s. Not wav file" % (in_file))
            return

        sr, original_data = sp.read_wave(in_file, first_channel=True)

        segments = self.segment_mapping_fn(in_file, sr)

        for segment in segments:
            if len(segment) == 3:
                start, end, seg_name = segment
                extra_info = None
            elif len(segment) == 4:
                start, end, seg_name, extra_info = segment
            else:
                self.log(logging.ERROR, "Failed %s. Segment length must be 3 or 4" % (in_file))
                return

            seg_path = os.path.join(self.out_dir, "%s.wav" % seg_name)
            sp.write_wav(seg_path, sr, original_data[start:end])

            extra_path = None
            if extra_info:
                extra_path = os.path.join(self.out_dir, "%s.txt" % seg_name)
                with open(extra_path, "w") as f:
                    f.write(extra_info)

            self.emit([seg_path, extra_path])


class PraatRunner(FileOutputNode):
    def run(self, in_file):
        self.log(logging.INFO, "Starting %s" % (in_file))

        out_file = self.derive_new_file_path(in_file, 'csv')

        if file_utils.should_run(in_file, out_file):
            cmd = ['praat', '--run', 'scripts/syllable_nuclei_v2.praat', os.path.abspath(in_file)]
            res = shell_run(cmd, stdout=out_file)

            if res != 0:
                self.log(logging.ERROR,"Failed %s -> %s with error code %i. cmd: %s" % (in_file, out_file, res, " ".join(cmd)))
                return

            self.log(logging.INFO, "Done %s -> %s" % (in_file, out_file))

        self.emit([out_file])


class KaldiASR(FileOutputNode):
    def setup(self, frame_subsampling_factor=3, max_active=7000, beam=15.0, lattice_beam=6.0, acoustic_scale=1.0, **kwargs):
        self.regex = re.compile(r"^utterance-id1 (.*)$", re.MULTILINE)
        self.error_regex = re.compile("^ERROR (.*)$", re.MULTILINE)

        cmd_args = {
            "kaldi_home": KALDI_HOME,
            "frame-subsampling-factor": frame_subsampling_factor,
            "max-active": max_active,
            "beam": beam,
            "lattice-beam": lattice_beam,
            "acoustic_scale": acoustic_scale
        }

        self.cmd = """{kaldi_home}/src/online2bin/online2-wav-nnet3-latgen-faster \
                    --online=false \
                    --do-endpointing=false \
                    --frame-subsampling-factor=3 \
                    --config={kaldi_home}/egs/aspire/s5/exp/tdnn_7b_chain_online/conf/online.conf \
                    --max-active=7000 \
                    --beam=15.0 \
                    --lattice-beam=6.0 \
                    --acoustic-scale=1.0 \
                    --word-symbol-table={kaldi_home}/egs/aspire/s5/exp/tdnn_7b_chain_online/graph_pp/words.txt \
                    {kaldi_home}/egs/aspire/s5/exp/tdnn_7b_chain_online/final.mdl \
                    {kaldi_home}/egs/aspire/s5/exp/tdnn_7b_chain_online/graph_pp/HCLG.fst \
                    'ark:echo utterance-id1 utterance-id1|' \
                    'scp:echo utterance-id1 **in_placeholder** |' \
                    'ark:/dev/null'""".format(**cmd_args)
        self.cmd = re.sub(' +',' ', self.cmd)


    def run(self, in_file):
        self.log(logging.INFO, "Starting %s" % (in_file))

        out_file = self.derive_new_file_path(in_file, 'txt')

        if file_utils.should_run(in_file, out_file):
            cmd = self.cmd.replace("**in_placeholder**", os.path.abspath(in_file))

            process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate()
            res = process.returncode


            if res == 0:
                hyp = self.regex.search(str(err).replace("\\n", "\n")).group(1)

                with open(out_file, "w") as f:
                    f.write(hyp)

                self.log(logging.INFO, "Done %s -> %s" % (in_file, out_file))
            else:
                error_lines = self.error_regex.findall(str(err).replace("\\n", "\n"))
                self.log(logging.ERROR,
                         "Failed %s -> %s with error code %i. cmd: %s. Error message: %s" % (
                         in_file, out_file, res, cmd, "\n\t".join(error_lines)))
                return

        self.emit([out_file])
