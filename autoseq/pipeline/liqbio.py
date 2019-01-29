from autoseq.pipeline.clinseq import ClinseqPipeline
from autoseq.tools.cnvcalling import LiqbioCNAPlot
from autoseq.util.clinseq_barcode import *
from autoseq.tools.structuralvariants import Svcaller, Sveffect, MantaSomaticSV
from autoseq.tools.umi import *
from autoseq.tools.alignment import fq_trimming, Realignment
from autoseq.util.library import find_fastqs

__author__ = 'thowhi'


class LiqBioPipeline(ClinseqPipeline):
    def __init__(self, sampledata, refdata, job_params, outdir, libdir, umi, maxcores=1, scratch="/scratch/tmp/tmp",
                 **kwargs):
        ClinseqPipeline.__init__(self, sampledata, refdata, job_params, outdir, libdir,
                                 maxcores, scratch, **kwargs)

        # Set the min alt frac value:
        self.default_job_params["vardict-min-alt-frac"] = 0.01
        self.default_job_params["vardict-min-num-reads"] = None
        self.default_job_params["vep-additional-options"] = " --pick --filter_common "

        # Remove clinseq barcodes for which data is not available:
        self.check_sampledata()

        if umi:
            self.configure_umi_processing()

        # Configure alignment and merging of fastq data for all clinseq barcodes:
        self.configure_align_and_merge()

        # Configure all panel analyses:
        self.configure_panel_analyses()

        # Configure liqbio-specific panel analyses:
        self.configure_panel_analyses_liqbio()

        # Configure additional msings analysis:
        self.configure_panel_msings_analyses()

        # Configure QC of all panel data:
        self.configure_all_panel_qcs()

        # Configure fastq QCs:
        self.configure_fastq_qcs()

        # Configure the low-pass whole genome analysis:
        self.configure_lowpass_analyses()

        # Configure low-pass whole genome data QC:
        self.configure_all_lowpass_qcs()

        # Configure MultiQC:
        self.configure_multi_qc()

    def configure_single_capture_analysis_liqbio(self, unique_capture):
        input_bam = self.get_capture_bam(unique_capture)
        sample_str = compose_lib_capture_str(unique_capture)

        # Configure svcaller analysis for each event type:
        for event_type in ["DEL", "DUP", "INV", "TRA"]:
            svcaller = Svcaller()
            svcaller.input_bam = input_bam
            svcaller.event_type = event_type
            svcaller.output_bam = "{}/svs/{}-{}.bam".format(self.outdir, sample_str, event_type)
            svcaller.output_gtf = "{}/svs/{}-{}.gtf".format(self.outdir, sample_str, event_type)
            svcaller.reference_sequence = self.refdata["reference_genome"]
            svcaller.scratch = self.scratch
            self.add(svcaller)

            self.set_capture_svs(unique_capture, event_type, (svcaller.output_bam, svcaller.output_gtf))

        # # FIXME: This code is kind of nasty, as the self.capture_to_results data structure is
        # # getting "pushed too far" in it's usage:
        # sveffect = Sveffect()
        # sveffect.input_del_gtf = self.capture_to_results[unique_capture].svs["DEL"][1]
        # sveffect.input_dup_gtf = self.capture_to_results[unique_capture].svs["DUP"][1]
        # sveffect.input_inv_gtf = self.capture_to_results[unique_capture].svs["INV"][1]
        # sveffect.input_tra_gtf = self.capture_to_results[unique_capture].svs["TRA"][1]
        # sveffect.ts_regions = self.refdata["ts_regions"]
        # sveffect.ar_regions = self.refdata["ar_regions"]
        # sveffect.fusion_regions = self.refdata["fusion_regions"]
        # sveffect.output_combined_bed = "{}/svs/{}_combined.bed".format(self.outdir, sample_str)
        # sveffect.output_effects_json = "{}/svs/{}_effects.json".format(self.outdir, sample_str)

        # self.add(sveffect)

        # self.set_capture_sveffect(unique_capture, sveffect.output_effects_json)

    def configure_panel_analyses_liqbio(self):
        # Configure liqbio analyses to be run on all unique panel captures individually:
        for unique_capture in self.get_mapped_captures_no_wgs():
            self.configure_single_capture_analysis_liqbio(unique_capture)

        # Configure a liqbio analyses for each normal-cancer pairing:
        for normal_capture in self.get_mapped_captures_normal():
            for cancer_capture in self.get_mapped_captures_cancer():
                self.configure_panel_analysis_cancer_vs_normal_liqbio(
                    normal_capture, cancer_capture)
    
    def configure_manta(self, normal_capture, cancer_capture):
        """
        Configure manta, to identify structural variants in sample

        :param normal_capture: A unique normal sample library capture
        :param cancer_capture: A unique cancer sample library capture
        """
        cancer_bam = self.get_capture_bam(cancer_capture)
        normal_bam = self.get_capture_bam(normal_capture)
        target_name = self.get_capture_name(cancer_capture.capture_kit_id)

        cancer_capture_str = compose_lib_capture_str(cancer_capture)
        normal_capture_str = compose_lib_capture_str(normal_capture)

        manta_sv = MantaSomaticSV()
        manta_sv.input_tumor = cancer_bam
        manta_sv.input_normal = normal_bam
        manta_sv.tumorid = cancer_capture_str
        manta_sv.normalid = normal_capture_str
        manta_sv.reference_sequence = self.refdata["reference_genome"]
        manta_sv.target_bed = self.refdata['targets'][target_name]['targets-bed-slopped20']
        manta_sv.output_dir = "{}/variants/{}-{}-manta-somatic".format(self.outdir, cancer_capture_str, normal_capture_str)

        self.add(manta_sv)


    def configure_liqbio_cna(self, normal_capture, cancer_capture):
        tumor_vs_normal_results = self.normal_cancer_pair_to_results[(normal_capture, cancer_capture)]
        tumor_results = self.capture_to_results[cancer_capture]

        # Configure the liqbio frankenplots:
        # NOTE: Get PureCN outputs:
        pureCN_outputs = self.normal_cancer_pair_to_results[(normal_capture, cancer_capture)].pureCN_outputs

        normal_str = compose_lib_capture_str(normal_capture)
        cancer_str = compose_lib_capture_str(cancer_capture)

        liqbio_cna = LiqbioCNAPlot()
        liqbio_cna.input_tumor_cnr = self.capture_to_results[cancer_capture].cnr
        liqbio_cna.input_tumor_cns = self.capture_to_results[cancer_capture].cns
        liqbio_cna.input_normal_cnr = self.capture_to_results[normal_capture].cnr
        liqbio_cna.input_normal_cns = self.capture_to_results[normal_capture].cns
        liqbio_cna.input_het_snps_vcf = self.normal_cancer_pair_to_results[(normal_capture, cancer_capture)].vcf_addsample_output
        liqbio_cna.input_purecn_csv = pureCN_outputs["csv"]
        liqbio_cna.input_purecn_genes_csv = pureCN_outputs["genes_csv"]
        liqbio_cna.input_purecn_loh_csv = pureCN_outputs["loh_csv"]
        liqbio_cna.input_purecn_variants_csv = pureCN_outputs["variants_csv"]
        liqbio_cna.input_svcaller_T_DEL = self.capture_to_results[cancer_capture].svs["DEL"][1]
        liqbio_cna.input_svcaller_T_DUP = self.capture_to_results[cancer_capture].svs["DUP"][1]
        liqbio_cna.input_svcaller_T_INV = self.capture_to_results[cancer_capture].svs["INV"][1]
        liqbio_cna.input_svcaller_T_TRA = self.capture_to_results[cancer_capture].svs["TRA"][1]
        liqbio_cna.input_svcaller_N_DEL = self.capture_to_results[normal_capture].svs["DEL"][1]
        liqbio_cna.input_svcaller_N_DUP = self.capture_to_results[normal_capture].svs["DUP"][1]
        liqbio_cna.input_svcaller_N_INV = self.capture_to_results[normal_capture].svs["INV"][1]
        liqbio_cna.input_svcaller_N_TRA = self.capture_to_results[normal_capture].svs["TRA"][1]
        liqbio_cna.input_germline_mut_vcf = self.get_vepped_germline_vcf(normal_capture) # Vepped germline variants
        liqbio_cna.input_somatic_mut_vcf = self.normal_cancer_pair_to_results[(normal_capture, cancer_capture)].vepped_vcf
        liqbio_cna.output_plot_png = "{}/qc/{}-{}-liqbio-cna.png".format(self.outdir, normal_str, cancer_str)
        liqbio_cna.output_cna_json = "{}/variants/{}-{}-liqbio-cna.json".format(self.outdir, normal_str, cancer_str)
        liqbio_cna.output_purity_json = "{}/qc/{}-{}-liqbio-purity.json".format(self.outdir, normal_str, cancer_str)
        self.add(liqbio_cna)

    def configure_panel_analysis_cancer_vs_normal_liqbio(self, normal_capture, cancer_capture):
        capture_name = self.get_capture_name(cancer_capture.capture_kit_id)

        self.configure_manta(normal_capture, cancer_capture)

        if self.refdata['targets'][capture_name]['purecn_targets']:
            self.configure_purecn(normal_capture, cancer_capture)
            self.configure_liqbio_cna(normal_capture, cancer_capture)


    def configure_umi_processing(self):

        capture_to_barcodes = self.get_unique_capture_to_clinseq_barcodes()
        for unique_capture in capture_to_barcodes.keys():
            capture_kit = unique_capture.capture_kit_id
            for clinseq_barcode in capture_to_barcodes[unique_capture]:
                trimmed_fqfiles = fq_trimming(self,
                                  fq1_files=find_fastqs(clinseq_barcode, self.libdir)[0],
                                  fq2_files=find_fastqs(clinseq_barcode, self.libdir)[1],
                                  clinseq_barcode=clinseq_barcode,
                                  ref=self.refdata['bwaIndex'],
                                  outdir= "{}/bams/{}".format(self.outdir, capture_kit),
                                  maxcores=self.maxcores)
            
            bam_file = self.configure_fastq_to_bam(fq_files=trimmed_fqfiles, 
                                                    clinseq_barcode=clinseq_barcode, 
                                                    capture_kit=capture_kit)
            realigned_bam = self.configure_alignment_with_umi(bamfile=bam_file, 
                                                    clinseq_barcode=clinseq_barcode, 
                                                    capture_kit=capture_kit, jobname='1')
            consensus_reads = self.configure_consensus_reads_calling(bam=realigned_bam, 
                                                    clinseq_barcode=clinseq_barcode,
                                                    capture_kit=capture_kit)
            realigned_bam2 = self.configure_alignment_with_umi(bamfile=consensus_reads, 
                                                    clinseq_barcode=clinseq_barcode, 
                                                    capture_kit=capture_kit, jobname='2')

    def configure_alignment_with_umi(self, bamfile, clinseq_barcode, capture_kit, jobname):

        align_unmap_bam = AlignUnmappedBam()
        align_unmap_bam.input_bam = bamfile
        align_unmap_bam.reference_genome = self.refdata['reference_genome']
        align_unmap_bam.output_bam = "{}/bams/{}/{}.mapped.bam".format(self.outdir, capture_kit, clinseq_barcode)
        align_unmap_bam.jobname = "alignment-of-unmapped-bam-"+ jobname
        self.add(align_unmap_bam)

        realingment = Realignment()
        realingment.input_bam = align_unmap_bam.output_bam
        realingment.output_bam = "{}/bams/{}/{}.realigned.bam".format(self.outdir, capture_kit, clinseq_barcode)
        realingment.reference_genome = self.refdata['reference_genome']
        realingment.known_indel1 = self.refdata['1KG']
        realingment.known_indel2 = self.refdata['Mills_and_1KG_gold_standard']
        realingment.target_intervals = "{}/bams/{}/{}.intervals".format(self.outdir, capture_kit, clinseq_barcode)
        realingment.jobname = "realignment-" + jobname
        self.add(realingment)

        return realingment.output_bam

    def configure_fastq_to_bam(self, fq_files, clinseq_barcode, capture_kit):

        library = parse_prep_id(clinseq_barcode)
        sample = compose_sample_str(extract_unique_capture(clinseq_barcode))

        fastq_to_bam = FastqToBam()
        fastq_to_bam.input_fastq1 = fq_files[0]
        fastq_to_bam.input_fastq2 = fq_files[1]
        fastq_to_bam.sample = sample
        fastq_to_bam.library = library
        fastq_to_bam.output_bam = "{}/bams/{}/{}.unmapped.bam".format(self.outdir, capture_kit, clinseq_barcode)
        self.add(fastq_to_bam)

        return fastq_to_bam.output_bam

    def configure_consensus_reads_calling(self, bam,  clinseq_barcode, capture_kit):

        group_reads = GroupReadsByUmi()
        group_reads.input_bam = bam
        group_reads.output_histogram = "{}/bams/{}/{}.grouped.bam.fs.txt".format(self.outdir, capture_kit, clinseq_barcode)
        group_reads.output_bam = "{}/bams/{}/{}.grouped.bam".format(self.outdir, capture_kit, clinseq_barcode)
        group_reads.jobname = "group-reads-by-umi"
        self.add(group_reads)

        call_consensus_reads = CallDuplexConsensusReads()
        call_consensus_reads.input_bam = group_reads.output_bam
        call_consensus_reads.output_bam = "{}/bams/{}/{}.consensus.bam".format(self.outdir, capture_kit, clinseq_barcode)
        call_consensus_reads.jobname = "call-duplex-consensus-reads"
        self.add(call_consensus_reads)

        return call_consensus_reads.output_bam






