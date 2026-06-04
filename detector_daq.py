"""Red Pitaya での検出器データ取得をまとめたクラス DetectorDAQ。

import rp
from rp_overlay import overlay
import detector_daq

fpga = overlay()
rp.rp_Init()
daq = detector_daq.DetectorDAQ(rp, N=16384, dec=rp.RP_DEC_1,
                               trigger_ch=0, trig_lvl=0.10, input_jumper="LV")
daq.calibrate_baseline(n_shots=32)        # ベースライン較正 (任意)
waveform = daq.acqADC()                    # 標準 ACQ で 1 ショット -> [ch1, ch2]
# DMA で取るとき:
daq.setup_dma()
waveform = daq.acqADC_DMA()                # DMA で 1 ショット -> [ch1, ch2]
daq.teardown_dma()
rp.rp_Release()

このモジュールは Red Pitaya 本体 (rp モジュールが使える環境) でのみ動きます。

設計方針: クラスは「ハードウェアからの 1 ショット取得」だけを担当します。
保存のしかた (CSV / .npz)、メタ情報、グラフ表示といった「実験の段取り」は、
学習しやすいようにノートブック側に残しています。

設定 (N, トリガレベル, 入力ジャンパなど) はコンストラクタの引数で渡し、
ベースラインや DMA バッファ位置などの状態は daq オブジェクトの中に持ちます
(以前はノートブックのグローバル変数に散らばっていました)。
"""
from __future__ import annotations

import time

import numpy as np


class DetectorDAQ:
    """Red Pitaya の 2 チャンネル取得 (標準 ACQ / DMA) をまとめたクラス。"""

    def __init__(self, rp, *, N=16384, dec=None, trigger_ch=0, trig_lvl=0.10,
                 input_jumper="LV", pre_trigger=30,
                 poll_sleep_s=1e-4, start_settle_s=1e-3):
        # --- 取得パラメータ ---
        self.rp = rp
        self.N = N                              # 取得バッファのサンプル数 (固定)
        self.dec = rp.RP_DEC_1 if dec is None else dec   # デシメーション (1 -> 125 MS/s)
        self.trigger_ch = trigger_ch            # トリガに使う ch のインデックス (0 -> CH1, 1 -> CH2)
        self.trig_lvl = trig_lvl                # トリガレベル [V] (ベースラインからの差分)
        self.input_jumper = input_jumper        # "LV" (±1V) または "HV" (±20V)
        self.pre_trigger = pre_trigger          # DMA: トリガ瞬間より前に残すサンプル数
        self.poll_sleep_s = poll_sleep_s        # トリガ/データ待ちの確認間隔 [秒]
        self.start_settle_s = start_settle_s    # 取得開始命令が機器に届くのを待つ時間 [秒]

        # --- 派生量 ---
        self.duty = N // 64                     # Option A で実際に使う波形長 [samples]
        self.trig_dly = N // 2 - 30             # 標準 ACQ 用トリガディレイ [samples] (現行挙動を維持)
        self.dma_data_size = N                  # DMA 1 ショットのサンプル数
        self.dma_trig_delay = self.dma_data_size - pre_trigger  # トリガ後に取り続けるサンプル数

        # --- 電圧変換・ゲイン (入力ジャンパで決まる) ---
        self.lsb_volts_by_jumper = {"LV": 1.0 / 8192.0, "HV": 20.0 / 8192.0}
        self.dma_lsb_volts = self.lsb_volts_by_jumper[input_jumper]
        # SDK ゲイン (LV=±1V -> RP_LOW, HV=±20V -> RP_HIGH)。これを設定しないと
        # 標準 ACQ の電圧読み出しとトリガレベル変換がジャンパに追従しない。
        # (定数名は機器の rp モジュールで要確認: 版により RP_LOW/RP_HIGH 以外の場合あり)
        self.gain_by_jumper = {"LV": rp.RP_LOW, "HV": rp.RP_HIGH}

        # --- トリガ／チャンネルマッピング ---
        self.trigger_list = [rp.RP_T_CH_1, rp.RP_T_CH_2]                         # トリガチャンネル定数
        self.acq_trig_sour_list = [rp.RP_TRIG_SRC_CHA_PE, rp.RP_TRIG_SRC_CHB_PE]  # 取得用トリガソース
        self.TRIG_SRC_NAMES = {
            rp.RP_TRIG_SRC_DISABLED: "RP_TRIG_SRC_DISABLED",
            rp.RP_TRIG_SRC_NOW:      "RP_TRIG_SRC_NOW",
            rp.RP_TRIG_SRC_CHA_PE:   "RP_TRIG_SRC_CHA_PE",
            rp.RP_TRIG_SRC_CHA_NE:   "RP_TRIG_SRC_CHA_NE",
            rp.RP_TRIG_SRC_CHB_PE:   "RP_TRIG_SRC_CHB_PE",
            rp.RP_TRIG_SRC_CHB_NE:   "RP_TRIG_SRC_CHB_NE",
            rp.RP_TRIG_SRC_EXT_PE:   "RP_TRIG_SRC_EXT_PE",
            rp.RP_TRIG_SRC_EXT_NE:   "RP_TRIG_SRC_EXT_NE",
            rp.RP_TRIG_SRC_AWG_PE:   "RP_TRIG_SRC_AWG_PE",
            rp.RP_TRIG_SRC_AWG_NE:   "RP_TRIG_SRC_AWG_NE",
        }

        # --- 状態 (較正で更新される / setup_dma で書き込まれる) ---
        self.baseline_ch1 = 0.0                 # 電圧ベースライン [V] (較正で実測して上書き)
        self.baseline_ch2 = 0.0
        self._dma_buf = {}                      # DMA バッファのチャネル別開始アドレス
        # 読み出しバッファをショット間で使い回す (毎回確保しない)
        self._dma_ibuf = [rp.i16Buffer(self.dma_data_size), rp.i16Buffer(self.dma_data_size)]

        # --- ハードウェア初期設定 ---
        self.reset()

    # ------------------------------------------------------------------ 設定
    def reset(self):
        """ハードウェアを初期設定する (リセット → ゲイン → トリガ出力 → 各種設定)。"""
        rp = self.rp
        rp.rp_AcqReset()

        # 入力ジャンパに合わせて両チャンネルのゲインを設定する (標準 ACQ の電圧・トリガを正す)。
        # rp_AcqReset() で既定 (LV) に戻るため、必ずこのリセットの後で設定する。
        rp.rp_AcqSetGain(rp.RP_CH_1, self.gain_by_jumper[self.input_jumper])
        rp.rp_AcqSetGain(rp.RP_CH_2, self.gain_by_jumper[self.input_jumper])

        # DIO0_N からトリガ信号を出力する設定 (外部機器との同期用)
        rp.rp_SetDpinEnableTrigOutput(True)
        rp.rp_SetSourceTrigOutput(rp.OUT_TR_ADC)

        # 標準 ACQ 設定 (Option A と確認プロット用)
        rp.rp_AcqSetDecimation(self.dec)
        self.apply_trigger_level()              # 初回はベースライン 0 のままなので絶対 = trig_lvl
        rp.rp_AcqSetTriggerDelay(self.trig_dly)

    @property
    def trigger_src(self):
        """trigger_ch で選ばれている取得用トリガソース定数。"""
        return self.acq_trig_sour_list[self.trigger_ch]

    @property
    def trigger_src_name(self):
        """トリガソースの名前 (.json 保存用)。"""
        return self.TRIG_SRC_NAMES[self.trigger_src]

    def _active_baseline(self):
        """trigger_ch で選ばれている ch の現在のベースライン [V] を返す。"""
        return (self.baseline_ch1, self.baseline_ch2)[self.trigger_ch]

    @property
    def threshold_abs_V(self):
        """FPGA に実際にセットされる絶対トリガしきい値 [V] (= ベースライン + trig_lvl)。"""
        return self._active_baseline() + self.trig_lvl

    def apply_trigger_level(self):
        """トリガレベルを (ベースライン + trig_lvl) に設定する。
        設定を変えたあとにこれを呼ぶと反映される。"""
        abs_level = self.threshold_abs_V
        self.rp.rp_AcqSetTriggerLevel(self.trigger_list[self.trigger_ch], abs_level)
        return abs_level

    def _start_and_wait(self, trigger_src, fill_checks):
        """1 ショットの取得を開始し、トリガ条件と全バッファの充填を待つ共通処理。"""
        rp = self.rp
        rp.rp_AcqStart()
        time.sleep(self.start_settle_s)
        rp.rp_AcqSetTriggerSrc(trigger_src)

        while rp.rp_AcqGetTriggerState()[1] != rp.RP_TRIG_STATE_TRIGGERED:
            time.sleep(self.poll_sleep_s)
        for check in fill_checks:
            while not check():
                time.sleep(self.poll_sleep_s)

    # --------------------------------------------------------------- 標準 ACQ
    def acqADC(self, trigger_src=None):
        """標準 ACQ で 1 ショット取得し、両チャンネルの波形 (numpy 配列, 長さ duty, V) を返す。"""
        rp = self.rp
        if trigger_src is None:
            trigger_src = self.trigger_src
        self._start_and_wait(trigger_src, [lambda: rp.rp_AcqGetBufferFillState()[1]])

        waveform_arr = []
        for channel in (rp.RP_CH_1, rp.RP_CH_2):
            fbuff = rp.fBuffer(self.N)
            rp.rp_AcqGetOldestDataV(channel, self.N, fbuff)
            data = np.fromiter(fbuff, dtype=np.float32, count=self.duty)
            waveform_arr.append(data)

        return waveform_arr

    # -------------------------------------------------------------------- DMA
    def _rewind(self, pos, ch_start, samples_back):
        """読み出し位置を samples_back サンプルだけ前にもどしたアドレスを返す。"""
        rel = (pos - ch_start - samples_back) % self.dma_data_size
        return ch_start + rel

    def setup_dma(self):
        """DMA を構成して両チャンネルで有効化する。Option B のループ前に 1 度だけ呼ぶ。"""
        rp = self.rp
        region = rp.rp_AcqAxiGetMemoryRegion()
        axi_start = region[1]            # DMA 用メモリの先頭アドレス
        axi_size  = region[2]            # DMA 用メモリの大きさ (サンプル数)
        print(f"DMA region: start=0x{axi_start:x} size_samples=0x{axi_size:x}")

        rp.rp_AcqAxiSetDecimationFactor(self.dec)

        # トリガのあと dma_trig_delay サンプルだけ取り続ける (残りはトリガ前の分として保持される)
        rp.rp_AcqAxiSetTriggerDelay(rp.RP_CH_1, self.dma_trig_delay)
        rp.rp_AcqAxiSetTriggerDelay(rp.RP_CH_2, self.dma_trig_delay)

        # DMA 用メモリを半分ずつ CH1/CH2 に割り当てる
        ch1_start = axi_start
        ch2_start = axi_start + axi_size // 2
        self._dma_buf['ch1'] = ch1_start
        self._dma_buf['ch2'] = ch2_start
        rp.rp_AcqAxiSetBufferSamples(rp.RP_CH_1, ch1_start, self.dma_data_size)
        rp.rp_AcqAxiSetBufferSamples(rp.RP_CH_2, ch2_start, self.dma_data_size)

        rp.rp_AcqAxiEnable(rp.RP_CH_1, True)
        rp.rp_AcqAxiEnable(rp.RP_CH_2, True)

        # トリガレベルを最新のベースラインで設定し直す
        self.apply_trigger_level()
        print("DMA enabled on CH1, CH2")

    def teardown_dma(self):
        """DMA を止める。Option B の最後に必ず呼ぶ。"""
        rp = self.rp
        rp.rp_AcqAxiEnable(rp.RP_CH_1, False)
        rp.rp_AcqAxiEnable(rp.RP_CH_2, False)

    def acqADC_DMA(self, trigger_src=None):
        """DMA で 1 ショット取得し、CH1・CH2 の波形 (電圧 [V]) を返す。

        トリガの瞬間から pre_trigger だけ前にもどして読み出すので、
        波形の先頭から pre_trigger サンプル目あたりにトリガの瞬間が来る。波形はそのままの電圧で返す。"""
        rp = self.rp
        if trigger_src is None:
            trigger_src = self.trigger_src
        self._start_and_wait(trigger_src, [
            lambda: rp.rp_AcqAxiGetBufferFillState(rp.RP_CH_1)[1],
            lambda: rp.rp_AcqAxiGetBufferFillState(rp.RP_CH_2)[1],
        ])

        rp.rp_AcqStop()

        # トリガの瞬間に書いていた位置
        pos1 = rp.rp_AcqAxiGetWritePointerAtTrig(rp.RP_CH_1)[1]
        pos2 = rp.rp_AcqAxiGetWritePointerAtTrig(rp.RP_CH_2)[1]

        # トリガ前の分だけもどして読み出す
        read1 = self._rewind(pos1, self._dma_buf['ch1'], self.pre_trigger)
        read2 = self._rewind(pos2, self._dma_buf['ch2'], self.pre_trigger)

        rp.rp_AcqAxiGetDataRaw(rp.RP_CH_1, read1, self.dma_data_size, self._dma_ibuf[0].cast())
        rp.rp_AcqAxiGetDataRaw(rp.RP_CH_2, read2, self.dma_data_size, self._dma_ibuf[1].cast())

        ch1 = (np.fromiter(self._dma_ibuf[0], dtype=np.int16, count=self.dma_data_size)
                 .astype(np.float32) * self.dma_lsb_volts)
        ch2 = (np.fromiter(self._dma_ibuf[1], dtype=np.int16, count=self.dma_data_size)
                 .astype(np.float32) * self.dma_lsb_volts)
        return [ch1, ch2]

    # ------------------------------------------------------------------ 較正
    def calibrate_baseline(self, n_shots=32, noise_win=1024):
        """ベースラインを較正し、トリガレベルを (ベースライン + trig_lvl) に合わせ直す。

        信号を待たず「すぐ取る」(RP_TRIG_SRC_NOW) で n_shots ショット取り、
        各ショットの先頭 noise_win サンプル (信号に関係ないノイズ) の中央値をベースラインとする。
        Ctrl+C で中断しても、それまでのショットで較正する。"""
        rp = self.rp
        if noise_win > self.dma_data_size:
            raise RuntimeError(
                f"noise_win={noise_win} は波形長 {self.dma_data_size} を超えています。小さくしてください。"
            )

        baselines_ch1, baselines_ch2 = [], []
        print(f"baseline 較正開始: 最大 {n_shots} ショット (Ctrl+C で中断可)")
        print(f"現在のトリガしきい値 (FPGA): {self.threshold_abs_V*1e3:.3f} mV "
              f"= baseline {self._active_baseline()*1e3:+.3f} mV + trig_lvl {self.trig_lvl*1e3:.3f} mV")

        self.setup_dma()
        try:
            try:
                for k in range(n_shots):
                    # 信号を待たず「すぐ取る」。較正でしきい値を決める前なので、本物のトリガを待つと
                    # 取得が止まってしまう。ベースラインはノイズの高さなので待つ必要もない。
                    wave_ch1, wave_ch2 = self.acqADC_DMA(rp.RP_TRIG_SRC_NOW)
                    # 波形の最初のほう (信号に関係ないノイズ) の中央値。median で外れ値に強い。
                    b1 = float(np.median(wave_ch1[:noise_win]))
                    b2 = float(np.median(wave_ch2[:noise_win]))
                    baselines_ch1.append(b1)
                    baselines_ch2.append(b2)
                    print(f"  shot {k+1:2d}/{n_shots}: ch1={b1*1e3:+7.3f} mV, ch2={b2*1e3:+7.3f} mV")
            except KeyboardInterrupt:
                print(f"中断 ({len(baselines_ch1)} ショット使用)")
        finally:
            self.teardown_dma()

        if not baselines_ch1:
            print("較正失敗: 1 ショットも取れませんでした。baseline は変更されません。")
            return

        arr1 = np.asarray(baselines_ch1)
        arr2 = np.asarray(baselines_ch2)
        self.baseline_ch1 = float(np.median(arr1))
        self.baseline_ch2 = float(np.median(arr2))
        new_threshold = self.apply_trigger_level()
        print()
        print(f"使用ショット数: {len(arr1)} / {n_shots}")
        print(f"ch1 baseline: median = {self.baseline_ch1*1e3:+7.3f} mV  (shot std = {arr1.std()*1e3:.3f} mV)")
        print(f"ch2 baseline: median = {self.baseline_ch2*1e3:+7.3f} mV  (shot std = {arr2.std()*1e3:.3f} mV)")
        print(f"\nFPGA トリガしきい値を更新: {new_threshold*1e3:.3f} mV "
              f"(= baseline {self._active_baseline()*1e3:+.3f} mV + trig_lvl {self.trig_lvl*1e3:.3f} mV)")

    # ---------------------------------------------------------------- メタ情報
    def metadata(self):
        """保存メタの共通項目 (取得条件) を返す。
        mode / n_shots / start_datetime など実行ごとに変わる項目はノート側で足す。"""
        return {
            'decimation_macro':      'RP_DEC_1',
            'trigger_ch_index':      self.trigger_ch,
            'trigger_src':           self.trigger_src_name,
            'trigger_level_delta_V': self.trig_lvl,            # ベースラインからの差分 (ユーザ設定)
            'trigger_level_abs_V':   self.threshold_abs_V,     # FPGA に実際にセットされた絶対値
            'input_range_jumper':    self.input_jumper,
        }
