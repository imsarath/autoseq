from pypedream.job import *
from pypedream.tools.unix import Cat

from autoseq.util.library import get_libdict
from autoseq.util.path import normpath

__author__ = 'dankle'


class Bwa(Job):
    def __init__(self):
        Job.__init__(self)
        self.input_fastq1 = None  # input ports must start with "input"
        self.input_fastq2 = None
        self.input_reference_sequence = None
        self.mark_secondary = None
        self.remove_duplicates = True
        self.readgroup = None
        self.output = None  # output ports must start with "output", can be "output_metrics", "output", etc
        self.duplication_metrics = None
        self.jobname = "bwa"

    def command(self):
        bwalog = self.output + ".bwa.log"
        samblasterlog = self.output + ".samblaster.log"
        tmpprefix = "{}/{}".format(self.scratch, uuid.uuid4())

        return "bwa mem -M -v 1 " + \
               required("-R ", self.readgroup) + \
               optional("-t ", self.threads) + \
               required(" ", self.input_reference_sequence) + \
               required(" ", self.input_fastq1) + \
               optional("", self.input_fastq2) + \
               required("2>", bwalog) + \
               "| samblaster -M --addMateTags " + \
               conditional(self.remove_duplicates, "--removeDups") + \
               optional("--metricsFile ", self.duplication_metrics) + \
               required("2>", samblasterlog) + \
               "| samtools view -Sb -u - " + \
               "| samtools sort " + \
               required("-T ", tmpprefix) + \
               optional("-@ ", self.threads) + \
               required("-o ", self.output) + \
               " - " + \
               " && samtools index " + self.output + \
               " && cat {} {}".format(bwalog, samblasterlog) + \
               " && rm {} {}".format(bwalog, samblasterlog)


class Skewer(Job):
    def __init__(self):
        Job.__init__(self)
        self.input1 = None
        self.input2 = None
        self.output1 = None
        self.output2 = None
        self.stats = None
        self.jobname = "skewer"

    def command(self):
        if not self.output1.endswith(".gz") or not self.output2.endswith(".gz"):
            raise ValueError("Output files need to end with .gz")

        tmpdir = os.path.join(self.scratch, "skewer-" + str(uuid.uuid4()))
        prefix = "{}/skewer".format(tmpdir)
        out_fq1 = prefix + "-trimmed-pair1.fastq.gz"
        out_fq2 = prefix + "-trimmed-pair2.fastq.gz"
        out_stats = prefix + "-trimmed.log"

        mkdir_cmd = "mkdir -p {}".format(tmpdir)

        skewer_cmd = "skewer -z " + \
                     optional("-t ", self.threads) + " --quiet " + \
                     required("-o ", prefix) + \
                     required("", self.input1) + \
                     optional("", self.input2)
        copy_output_cmd = "cp " + out_fq1 + " " + self.output1 + \
            conditional(self.input2, " && cp " + out_fq2 + " " + self.output2)

        copy_stats_cmd = "cp " + out_stats + " " + self.stats
        rm_cmd = "rm -r {}".format(tmpdir)
        return " && ".join([mkdir_cmd, skewer_cmd, copy_output_cmd, copy_stats_cmd, rm_cmd])


def align_library(pipeline, fq1_files, fq2_files, lib, ref, outdir, maxcores=1,
                  remove_duplicates=True):
    """
    Align fastq files for a PE library
    :param remove_duplicates:
    :param pipeline:
    :param fq1_files:
    :param fq2_files:
    :param lib:
    :param ref:
    :param outdir:
    :param maxcores:
    :return:
    """
    if not fq2_files:
        logging.debug("lib {} is SE".format(lib))
        return align_se(pipeline, fq1_files, lib, ref, outdir, maxcores, remove_duplicates)
    else:
        logging.debug("lib {} is PE".format(lib))
        return align_pe(pipeline, fq1_files, fq2_files, lib, ref, outdir, maxcores, remove_duplicates)


def align_se(pipeline, fq1_files, lib, ref, outdir, maxcores, remove_duplicates=True):
    """
    Align single end data
    :param pipeline:
    :param fq1_files:
    :param lib:
    :param ref:
    :param outdir:
    :param maxcores:
    :param remove_duplicates:
    :return:
    """
    logging.debug("Aligning files: {}".format(fq1_files))
    fq1_abs = [normpath(x) for x in fq1_files]
    fq1_trimmed = []
    for fq1 in fq1_abs:
        skewer = Skewer()
        skewer.input1 = fq1
        skewer.input2 = None
        skewer.output1 = outdir + "/skewer/{}".format(os.path.basename(fq1))
        skewer.stats = outdir + "/skewer/skewer-stats-{}.log".format(os.path.basename(fq1))
        skewer.threads = maxcores
        skewer.jobname = "skewer/{}".format(os.path.basename(fq1))
        skewer.scratch = pipeline.scratch
        skewer.is_intermediate = True
        fq1_trimmed.append(skewer.output)
        pipeline.add(skewer)

    cat1 = Cat()
    cat1.input = fq1_trimmed
    cat1.output = outdir + "/skewer/{}_1.fastq.gz".format(lib)
    cat1.jobname = "cat/{}".format(lib)
    cat1.is_intermediate = False
    pipeline.add(cat1)

    bwa = Bwa()
    bwa.input_fastq1 = cat1.output
    bwa.input_reference_sequence = ref
    bwa.remove_duplicates = remove_duplicates
    libdict = get_libdict(lib)
    rg_lb = "{}-{}-{}-{}".format(libdict['sdid'], libdict['type'], libdict['sample_id'], libdict['prep_id'])
    rg_sm = "{}-{}-{}".format(libdict['sdid'], libdict['type'], libdict['sample_id'])
    rg_id = lib
    bwa.readgroup = "\"@RG\\tID:{rg_id}\\tSM:{rg_sm}\\tLB:{rg_lb}\\tPL:ILLUMINA\"".format(rg_id=rg_id, rg_sm=rg_sm,
                                                                                          rg_lb=rg_lb)
    bwa.threads = maxcores
    bwa.output = "{}/{}.bam".format(outdir, lib)
    bwa.scratch = pipeline.scratch
    bwa.jobname = "bwa/{}".format(lib)
    bwa.is_intermediate = False
    pipeline.add(bwa)

    return bwa.output


def align_pe(pipeline, fq1_files, fq2_files, lib, ref, outdir, maxcores=1, remove_duplicates=True):
    """
    align paired end data
    :param pipeline:
    :param fq1_files:
    :param fq2_files:
    :param lib:
    :param ref:
    :param outdir:
    :param maxcores:
    :param remove_duplicates:
    :return:
    """
    fq1_abs = [normpath(x) for x in fq1_files]
    fq2_abs = [normpath(x) for x in fq2_files]
    logging.debug("Trimming {} and {}".format(fq1_abs, fq2_abs))
    pairs = [(fq1_abs[k], fq2_abs[k]) for k in range(len(fq1_abs))]

    fq1_trimmed = []
    fq2_trimmed = []

    for fq1, fq2 in pairs:
        skewer = Skewer()
        skewer.input1 = fq1
        skewer.input2 = fq2
        skewer.output1 = outdir + "/skewer/libs/{}".format(os.path.basename(fq1))
        skewer.output2 = outdir + "/skewer/libs/{}".format(os.path.basename(fq2))
        skewer.stats = outdir + "/skewer/libs/skewer-stats-{}.log".format(os.path.basename(fq1))
        skewer.threads = maxcores
        skewer.jobname = "skewer/{}".format(os.path.basename(fq1))
        skewer.scratch = pipeline.scratch
        skewer.is_intermediate = True
        fq1_trimmed.append(skewer.output1)
        fq2_trimmed.append(skewer.output2)
        pipeline.add(skewer)

    cat1 = Cat()
    cat1.input = fq1_trimmed
    cat1.output = outdir + "/skewer/{}-concatenated_1.fastq.gz".format(lib)
    cat1.jobname = "cat1/{}".format(lib)
    cat1.is_intermediate = True
    pipeline.add(cat1)

    cat2 = Cat()
    cat2.input = fq2_trimmed
    cat2.jobname = "cat2/{}".format(lib)
    cat2.output = outdir + "/skewer/{}-concatenated_2.fastq.gz".format(lib)
    cat2.is_intermediate = True
    pipeline.add(cat2)

    bwa = Bwa()
    bwa.input_fastq1 = cat1.output
    bwa.input_fastq2 = cat2.output
    bwa.input_reference_sequence = ref
    bwa.remove_duplicates = remove_duplicates
    libdict = get_libdict(lib)
    rg_lb = "{}-{}-{}-{}".format(libdict['sdid'], libdict['type'], libdict['sample_id'], libdict['prep_id'])
    rg_sm = "{}-{}-{}".format(libdict['sdid'], libdict['type'], libdict['sample_id'])
    rg_id = lib
    bwa.readgroup = "\"@RG\\tID:{rg_id}\\tSM:{rg_sm}\\tLB:{rg_lb}\\tPL:ILLUMINA\"".format(rg_id=rg_id, rg_sm=rg_sm,
                                                                                          rg_lb=rg_lb)
    bwa.threads = maxcores
    bwa.output = "{}/{}.bam".format(outdir, lib)
    bwa.jobname = "bwa/{}".format(lib)
    bwa.scratch = pipeline.scratch
    bwa.is_intermediate = False
    pipeline.add(bwa)

    return bwa.output
