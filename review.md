# コードレビュー — Red Pitaya 検出器ノートブック群

対象ファイル:

- `detector-acquisition-dma.ipynb` — データ取得（標準 ACQ + DMA）
- `detector-analysis-dma.ipynb` — `.npz` 全波形の解析（相互相関・サブサンプル遅延）
- `detector-analysis-acq.ipynb` — `.dat` ピーク／積分の解析
- `paper_style.py` — Plotly テンプレート

レビューの粒度: ノートブックごとに **構造の所感 → 良い点 → 指摘（優先度: 高 / 中 / 低）** の順にまとめます。最後に、ご質問の **「CH1 または CH2 のどちらかでトリガしたい（OR トリガ）」** について、Red Pitaya の API 仕様を踏まえた現実的な実装案を 5 つ整理して回答します。

---

## 0. 全体の総評

3 ノートブックは「教材としての読みやすさ」と「実運用としての軽さ」のバランスが非常によく取れています。以下は共通の良い点です。

- マークダウンセル → コードセル → 結果という一定のリズムで、初めての読者でも上から実行できる。
- `paper_style.py` を全ノートブックで共有しており、図の体裁が統一されている。
- 取得時メタデータ（トリガ条件、サンプリング周期、ジャンパ設定）を **同名 `.json`** に書き出す設計で、解析側はそれを読み取って動作する。再現性の観点で正解。
- `find_latest()` で「`./data/` の最新ファイルを開く」運用が定着しており、ファイルパスを毎回貼り替える必要がない。

共通の改善余地は以下の 3 点です。詳細は各ノートブックの節で述べます。

1. ~~**取得側ノートブックでハードウェアトリガを待つ `while` ループにタイムアウトがない**~~ — トリガ条件が状況依存で適切なタイムアウト値を決めにくいため、ハングしたら手動 `KeyboardInterrupt` で止める方針で **意図的にそのまま**（運用上の判断）。
2. ✅ **対応済み**: `acqADC` / `acqADC_DMA` から `rp.rp_GenTriggerOnly()` を削除（OUT1 を使わない運用のため不要）。
3. **ベースライン（オフセット）の扱いが解析側で一貫していない**。`detector-analysis-dma.ipynb` の §7 では `a - a.mean()` で全波形平均を引いているが、信号成分が窓内に含まれる場合は平均自体が信号で歪む。プリトリガ領域の平均を使うのが正攻法。

---

## 1. `detector-acquisition-dma.ipynb`

### 構造

セル順は「導入 → 用語説明 → ライブラリ／FPGA 読み込み → 信号発生器設定 + 標準 ACQ 関数 → 動作確認プロット → DMA セットアップ + 取得関数 → 動作確認プロット → Option A 計測 → Option B 計測 → クリーンアップ → トラブルシューティング」と非常に整っています。教材として通読するときも、本番計測でセル単位で実行するときも、両方が成立する流れになっています。

### 良い点

- **ジャンパ位置（LV/HV）に対する注意書きが冒頭に明記**。実機運用で最も間違えやすい箇所をきちんと押さえている。
- 標準 ACQ と DMA の振る舞いの違いを **表で並べて** いる（cell-12 のマークダウン）。
- DMA セットアップ／撤収を `setup_dma()` / `teardown_dma()` に切り出し、Option B の本番ループは `try / finally` で必ず後始末される。
- Option B の `KeyboardInterrupt` 後にメタデータを生成して `.json` に保存している。**本ノートブックの再現性の中核**であり、ここはとても良い。
- 読み出しバッファ `_dma_ibuf` をモジュールスコープで 1 度確保してショット間で使い回している。アロケーション分のオーバーヘッドが減る。

### 指摘事項

#### 高優先度

##### H1. ~~トリガ待ち `while` ループにタイムアウトがない~~（対応見送り）

トリガが来るまでの時間が状況依存で「妥当なタイムアウト値」を一意に決めにくいため、**意図的にそのまま**運用する方針です。ハング時は Jupyter の停止ボタン (`KeyboardInterrupt`) で止めてください。以下、参考までに当初検討した内容を残しておきます（実装は不要）。

該当箇所は `acqADC()`（cell-9）と `acqADC_DMA()`（cell-13）の 4 箇所:

```python
while rp.rp_AcqGetTriggerState()[1] != rp.RP_TRIG_STATE_TRIGGERED:
    time.sleep(_POLL_SLEEP_S)
while not rp.rp_AcqGetBufferFillState()[1]:
    time.sleep(_POLL_SLEEP_S)
# DMA 側はさらに
while not rp.rp_AcqAxiGetBufferFillState(rp.RP_CH_1)[1]:
    time.sleep(_POLL_SLEEP_S)
while not rp.rp_AcqAxiGetBufferFillState(rp.RP_CH_2)[1]:
    time.sleep(_POLL_SLEEP_S)
```

トリガレベルを高く設定しすぎたとき、または信号が来ないとき、これらは永久に回り続けます。Jupyter の停止ボタンが効かないこともあります。**1 ショットあたりの想定最大時間** を引数で渡し、超えたら `RuntimeError` か `TimeoutError` を投げる形を推奨します:

```python
def _wait_until(predicate, timeout_s: float, what: str):
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what} が {timeout_s:.1f} 秒待っても来ません。"
                               "トリガレベル / 結線 / ジャンパを確認してください。")
        time.sleep(_POLL_SLEEP_S)
```

`acqADC_DMA(timeout_s=1.0)` のような使い方ができ、本番計測ループ側で `except TimeoutError` を捕まえてログに残す運用にすれば、ハングではなく自己回復可能になります。

##### H2. ✅ ~~`rp.rp_GenTriggerOnly()` が本番計測でも毎ショット呼ばれる~~（対応済み）

OUT1 を使わない運用のため、`acqADC` / `acqADC_DMA` から `rp.rp_GenTriggerOnly()` 呼び出しを削除しました。loopback 動作確認セル (cell-11, cell-15) は外部信号源があれば従来通り動作します（信号が来なければトリガ待ちのまま）。

以下、もとの検討内容（履歴）。

`acqADC` / `acqADC_DMA` の中で:

```python
rp.rp_AcqSetTriggerSrc(trigger_src)
rp.rp_GenTriggerOnly(channel_list[trigger_ch])  # 信号発生器をトリガ
```

`rp_GenTriggerOnly()` は **対応する信号発生器（OUT1 / OUT2）を 1 度だけ駆動するコマンド** です。loopback 検証では必須ですが、本番計測では:

- OUT1（または OUT2）に意図せず信号が出続ける（プローブで監視していると気づきますが、見落とすと検出器の前段に influent しかねない）
- 信号発生器が `rp_GenAmp` などで初期化されていなければエラーは出ないが「無音のトリガ」が無駄に走る

**取得関数を「loopback 用」と「本番計測用」で分岐できるようにする** のがクリーンです。最小修正案:

```python
def acqADC_DMA(trigger_src, *, gen_pulse: bool = False):
    rp.rp_AcqStart()
    time.sleep(0.001)
    rp.rp_AcqSetTriggerSrc(trigger_src)
    if gen_pulse:
        rp.rp_GenTriggerOnly(channel_list[trigger_ch])  # loopback テスト用
    ...
```

動作確認セル（cell-15）は `gen_pulse=True`、本番ループ（cell-19）は `gen_pulse=False` で呼ぶ。この一行で本番計測の安全性が大きく上がります。

#### 中優先度

##### M1. ✅ ~~パラメータが各セルに散在している~~（対応済み）

cell-9 の冒頭に `# === 設定 (Config) ===` セクションを設け、取得パラメータ・DMA パラメータ・入力ジャンパ・トリガ／チャンネルマッピング・トリガソース名表・タイミング定数を 1 か所に集約しました。cell-13 の DMA パラメータも cell-9 に移動済みで、cell-13 は `_dma_ibuf` の確保と関数定義のみになっています。

以下、もとの検討内容（履歴）。

cell-9 にデシメーション、トリガレベル、`trig_dly`、`duty`、`_POLL_SLEEP_S` が、cell-13 に `DMA_DATA_SIZE`、`DMA_TRIG_DELAY`、`_DMA_LSB_VOLTS` が分散しています。読者がパラメータを変えたときに「どのセルを再実行すべきか」が把握しづらいです。冒頭に `Config = dict(...)`（または `dataclass`）で一括宣言し、`acqADC` / `acqADC_DMA` / `setup_dma` がそれを参照する形にすると、再現性とメタデータ保存も簡単になります。

##### M2. ✅ ~~メタデータ `trigger_src` の文字列が手書きの三項演算~~（対応済み）

cell-9 に `TRIG_SRC_NAMES`（定数 → 名称の逆引き辞書）を導入し、cell-17 / cell-19 の メタデータ生成箇所を `TRIG_SRC_NAMES[acq_trig_sour_list[trigger_ch]]` に置換しました。`acq_trig_sour_list` を `RP_TRIG_SRC_EXT_PE` などに差し替えても、メタの文字列が自動追従します。

以下、もとの検討内容（履歴）。

cell-17 と cell-19:

```python
'trigger_src': 'RP_TRIG_SRC_CHA_PE' if trigger_ch == 0 else 'RP_TRIG_SRC_CHB_PE',
```

`acq_trig_sour_list` を別の値（例えば `RP_TRIG_SRC_EXT_PE` を入れた）に書き換えたとき、メタの `trigger_src` 表記がコードと食い違います。次のように派生させると食い違いが出ません:

```python
_TRIG_SRC_NAMES = {
    rp.RP_TRIG_SRC_CHA_PE: 'RP_TRIG_SRC_CHA_PE',
    rp.RP_TRIG_SRC_CHB_PE: 'RP_TRIG_SRC_CHB_PE',
    rp.RP_TRIG_SRC_EXT_PE: 'RP_TRIG_SRC_EXT_PE',
    # ...
}
'trigger_src': _TRIG_SRC_NAMES[acq_trig_sour_list[trigger_ch]],
```

##### M3. ✅ ~~`_DMA_LSB_VOLTS` が LV 前提のハードコード~~（対応済み）

cell-9 に `INPUT_JUMPER = "LV"` と `LSB_VOLTS_BY_JUMPER = {"LV": 1.0/8192.0, "HV": 20.0/8192.0}` を導入し、`_DMA_LSB_VOLTS = LSB_VOLTS_BY_JUMPER[INPUT_JUMPER]` で派生させる形にしました。HV 運用時は `INPUT_JUMPER = "HV"` の 1 行を変えれば、LSB 換算とメタデータの両方が一括で切り替わります。

以下、もとの検討内容（履歴）。

cell-13:

```python
_DMA_LSB_VOLTS = 1.0 / 8192.0  # 14-bit 符号付き ADC: ±1V (LV) なら 1/8192。±20V (HV) なら 20/8192
```

ジャンパが HV なのに書き換え忘れた場合、ボルト換算が 20 倍ずれて気づきにくいです。M1 の Config 化と組み合わせて `jumper = 'LV' or 'HV'` から派生させるのが安全です（ジャンパ位置はソフトでは検出不能なので、最終的には人間が指定する値ですが、設定値が一箇所で済めば食い違いは起きにくくなります）。

##### M4. ✅ ~~`acqADC` と `acqADC_DMA` の重複~~（対応済み）

cell-9 に共通ヘルパ `_start_and_wait(trigger_src, fill_checks)` を切り出し、`acqADC` と `acqADC_DMA` の両方から呼ぶようにしました。「Start → Settle → SetTriggerSrc → トリガ待ち → 充填待ち」のシーケンスが一箇所にまとまっています。`fill_checks` は callable のリストで、標準 ACQ なら `rp_AcqGetBufferFillState`、DMA なら CH1/CH2 の `rp_AcqAxiGetBufferFillState` を渡す形です。

以下、もとの検討内容（履歴）。

「Start → Sleep → SetTriggerSrc → GenTriggerOnly → トリガ待ち → バッファ充填待ち → 取り出し」の構造が同じです。共通の `_wait_for_trigger()` ヘルパに切り出すと、H1 のタイムアウト導入も両方に同時に効かせられます。

##### M5. ✅ ~~`time.sleep(0.001)` と `_POLL_SLEEP_S = 1e-4` の不整合~~（対応済み）

cell-9 に `_START_SETTLE_S = 1e-3` を導入し、`rp_AcqStart()` 直後の `time.sleep(0.001)` をこの定数に置き換えました。コメントで「`rp_AcqStart()` 命令が FPGA に到達するのを待つための遅延。この間に `rp_AcqSetTriggerSrc()` を呼ぶと取りこぼすことがあるため挟んでいる（本機での経験値）」と意図を残しています。

以下、もとの検討内容（履歴）。

`rp.rp_AcqStart()` 直後に固定 1 ms の `time.sleep` が入っていますが、これは「Start を確実に確定させるためのおまじない」です。実機での必要時間が分かれば定数化しコメントを残すか、API 側にハンドシェイクがあるならそれを使うのが望ましいです。

#### 低優先度

- **L1**: `np.fromiter(_dma_ibuf[0], dtype=np.int16, count=DMA_DATA_SIZE)` は Python レベルでイテレーションするので、波形が長くなると無視できないオーバーヘッドになります。`memoryview` 経由か `np.frombuffer` でゼロコピー化できないかは、`_dma_ibuf` のバインディングが `__buffer__` を持っているかに依存します。短波形（16384 サンプル）なら今のままで実用上問題ありません。
- **L2**: cell-17（Option A）と cell-19（Option B）は 70 行近くあります。どちらも `start_date` の生成、`out_path / meta_path` 構築、メタ辞書、メインループ、保存処理を含んでいます。`run_logging_loop(acqADC_func, save_format='dat' | 'npz', meta_extra={...})` のようにヘルパ化すると見通しがよくなり、Option C（後述する OR トリガ実装案）を追加するときも同じ枠組みで済みます。
- **L3**: 教育的なコメントが多いので、本番計測用のスリム版を用意しても良いかもしれません（必須ではありません）。
- **L4**: `rp.rp_Release()` がクリーンアップセル（cell-21）にしかなく、Option B のループから直接抜けたあとカーネルを再起動せずに別のオーバーレイを試すと FPGA がロック状態になることがあります。`rp_Release` を `try/except` で安全に再呼び出せるようにしておくと運用が楽です。

---

## 2. `detector-analysis-dma.ipynb`

### 構造

「読み込み → 1 イベント表示 → 複数イベント並べ → 平均化 → argmax 時間差 → 補間 → 相互相関 1 イベント → 相互相関 + 放物線 全イベント」と段階的に進む構成。各セクションが独立して実行でき、教材としても解析テンプレートとしても優秀です。

### 良い点

- §3 の平均化で **ベースラインノイズの 1/√N 低減** を理論値と一緒に表示しているのが良い。受講者が「平均化の効能」を体感できる。
- §6 で `np.correlate(b, a, ...)` を使う理由（ラグの向きの定義）を脚注付きで解説しているのは丁寧。
- §7 の放物線フィットによるサブサンプル補正は実用レベル。`known_delay` がメタにあれば差分を表示する仕掛けまで入っており、フィクスチャテストもしやすい。

### 指摘事項

#### 中優先度

##### M6. ベースライン差し引きが「波形全体平均」になっている

§6 / §7 で:

```python
a = ch1[event_idx] - ch1[event_idx].mean()
b = ch2[event_idx] - ch2[event_idx].mean()
```

信号が窓内に含まれる場合、`mean()` 自体が信号で押し上げられるため、ベースラインが正しく引けません。**プリトリガ領域（信号がまだ来ていない区間）の平均** で引くのが標準的:

```python
nb = samples_per_shot // 4  # 先頭 1/4 をベースライン領域とみなす
a = ch1[event_idx] - ch1[event_idx, :nb].mean()
b = ch2[event_idx] - ch2[event_idx, :nb].mean()
```

§3 では既に `nb = samples_per_shot // 4` を使っているので、この値を §6/§7 でも流用すれば一貫します。

##### M7. 全波形 16384 サンプルでの相互相関は計算量が大きい

`np.correlate(..., mode='full')` は時間領域の畳み込みで O(N²)。N=16384 だと 1 イベント約 0.27 GFLOPs。`scipy.signal.correlate(method='fft')` を使えば O(N log N) で 100 倍以上速くなります。Red Pitaya のオンボード Python は CPU が非力なので、ショット数が増えると体感差が大きいです。

代替案として、§4 の argmax で得たラフなピーク位置を中心に **±数百サンプルの窓** に切り出してから相互相関を取る方法もあります（コメントで「発展課題」と触れていますが、実装してしまっても良いと思います）。

##### M8. `parabolic_peak_offset` で `denom == 0` のときの挙動

```python
if denom == 0:
    return 0.0
```

flat な相関ピークになる物理状況（信号が無い、ノイズだけ）はそもそも遅延を語る意味がないので、`np.nan` を返してヒストグラムから自然に外す、もしくは `RuntimeWarning` を出すのが好みです。0.0 を返すと「遅延 0」が偽って積み上がるので、ヒストグラムの 0 ns 付近の山が水増しされます。

#### 低優先度

- **L5**: §7 のループは Python レベルで n_shots 回。`scipy.signal.correlate(b, a, mode='full', method='fft')` を使うか、もしくは `scipy.signal.fftconvolve` 系で 2D バッチ化するともっと速くなります。
- **L6**: §4 の argmax 時間差は閾値カット（max が一定値以上のショットのみ）と組み合わせると、暗計数（ノイズしか拾えていない事象）を除外できて分布がぐっとクリーンになります。コメントで「発展課題」として触れているので、サンプルコードを置いておくと親切です。
- **L7**: § ヘッダ番号と本文の章立て（"§4", "§7"）が他ノートブックと表記揺れあり。気にならない範囲ですが統一すると見やすいです。

---

## 3. `detector-analysis-acq.ipynb`

### 構造

「読み込み → 統計サマリ → ヒストグラム（sum / max を 4 枚並べ） → 時間変動 → 相関散布図 → 閾値カット」と非常にきれい。Option A の `.dat` ファイルに対する標準的な眺め方を一通り押さえています。

### 良い点

- ヒストグラムのビン幅を **両チャンネルで揃える** ロジック（`shared_xbins`）は正しいアプローチ。CH1 / CH2 を比較するときの定石。
- 散布図に `Scattergl` を使っているのでショット数が多くても重くならない。
- `scaleanchor='y'` で **1:1 アスペクト** を強制している（直近のコミットで入った修正のようですね）。同じ単位の量を比較するときに重要で、これがないと「相関が 1 に近い」のにプロットが斜め方向に潰れて見えるという現象が起きます。
- 統計サマリ（cell-10）で `mean / std / quartile / min / max` を pandas 抜きで表示。Red Pitaya 上で pandas を入れずに済む工夫が効いている。

### 指摘事項

#### 中優先度

##### M9. `np.genfromtxt` の代わりに `np.loadtxt` か NPZ 化を検討

`np.genfromtxt` は柔軟ですが遅いです。Option A の `.dat` は型が固定（全列 `float64`）かつ行数 = ショット数なので、何万行になるとロード時間が体感できる差になります。代替案:

1. `np.loadtxt(dat_path, delimiter=',', skiprows=1)` + 別途 `data.dtype.names = (...)` で構造化配列化（`genfromtxt` の数倍速い）。
2. もしくは取得側で **`.dat` ではなく `.npz`** に書く（既に Option B でやっているのと同じ構造）。

##### M10. 自動ビン幅 `(hi - lo) / 30` は物理的根拠が弱い

`_auto_bin_width` の既定値は固定 30 ビンですが、Freedman-Diaconis（IQR ベース）や Sturges（log2 N）の方が分布の形に応じて妥当な値になります。

```python
def _auto_bin_width_fd(arr: np.ndarray) -> float:
    iqr = np.subtract(*np.percentile(arr, [75, 25]))
    return 2 * iqr * arr.size ** (-1/3) if iqr > 0 else (arr.max() - arr.min()) / 30
```

学習用としては「30 ビン固定」も悪くありませんが、`detector-analysis-dma.ipynb` §7 でも同じ `range / 30` 自動値が使われているので、共通ヘルパに切り出して双方で同じ振る舞いにできると良いです。

#### 低優先度

- **L8**: `threshold = 0.10` は手動書き換え式。`ipywidgets` のスライダ + `interact` を使うとリアルタイムで動かせて教材として刺さります（追加依存が増えるので必須ではありません）。
- **L9**: §5 の閾値カットは `max_ch1` のみ。**コインシデンス（CH1 と CH2 両方が閾値超）** のセルが「次のチャレンジ」として言及されていますが、半行で実装できるのでサンプルコードを書き添えても良いかもしれません:

  ```python
  mask_coinc = (data["max_ch1"] > thr1) & (data["max_ch2"] > thr2)
  ```

  この実装が後述の OR トリガ検討にも直結します。

---

## 4. `paper_style.py`

### 良い点

- **Okabe-Ito パレット** を採用している点。色覚多様性に配慮した論文向け配色として広く使われている。
- 4 辺枠 + 内向き目盛り + グリッドなしで論文体裁。
- `figsize(rows, cols)` で複数パネルでもアスペクト比が破綻しないヘルパが用意されている。

### 指摘事項

#### 低優先度

##### L10. ドキュストリングと実装が食い違っている

ファイル冒頭:

```python
"""...
Aspect ratio: 25:9 (banner-style, suited to long time-series waveforms).
"""
```

ですが実装は `ASPECT_RATIO = (16, 9)`、`WIDTH = 1280`、`HEIGHT = 720` で **16:9**。さらに `figsize` 関数の docstring も:

```python
"""For a single plot: figsize() -> (1000, 360).
For 2 stacked plots: figsize(rows=2) -> (1000, 720).
For 1x2 side-by-side: figsize(cols=2) -> (1000, 180).
"""
```

これも 1000 px 幅前提で、現在の 1280 px と食い違っています（昔の値の名残のようです）。`(1280, 720)` / `(1280, 1440)` / `(1280, 360)` に直すか、docstring から数値例を削除するのが安全です。

##### L11. `show()` の docstring に「25:9 aspect ratio」と書いてある

L10 と同根。`16:9` に統一してください。

##### L12. `figsize(cols=2)` の挙動

`panel_w = WIDTH * scale / cols`、`panel_h = panel_w * ratio[1] / ratio[0]`、戻り値が `int(panel_w * cols), int(panel_h * rows)` なので:

- `figsize()` → `(1280, 720)` ✅
- `figsize(rows=2)` → `(1280, 1440)` ✅（縦に 2 段、各パネル 16:9）
- `figsize(cols=2)` → `(1280, 360)` ✅（横に 2 段、合計幅 1280、各パネル 640 × 360）

意図通りです。docstring（L10）が古いだけ。実装は正しいです。

---

## 5. **CH1 / CH2 OR トリガの実現可能性**（ご質問への回答）

ご質問:

> RedPitaya のチャンネル 1 にトリガをかけるような形にしていますが、チャンネル 1 かチャンネル 2 のどちらかが引っかかった時にトリガする、といったことが可能なのか

### 結論

**Red Pitaya 125-14 のデフォルト FPGA イメージ + 公式 Python (`rp.rp_*`) / SCPI API の範囲では、CH1 OR CH2 を 1 つのトリガソースとして指定する直接的な手段は存在しません。**

これは `detector-acquisition-dma.ipynb` cell-6 のマクロ一覧でも明らかで、トリガソースは `RP_TRIG_SRC_DISABLED / NOW / CHA_PE / CHA_NE / CHB_PE / CHB_NE / EXT_PE / EXT_NE / AWG_PE / AWG_NE` の 10 種類のみ、**「CHA OR CHB」に相当する定数はありません**。`rp.rp_AcqSetTriggerSrc()` は 1 つのソースしか取れません。

ただし、ユーザの要求を満たす **回避策（workaround）が複数あります**。下表で整理し、以降で個別に詳解します。

| 案 | 概要 | 必要工数 | 真の OR か | デッドタイム | 推奨度 |
|---|---|---|---|---|---|
| **A. 外部 OR ゲート + EXT トリガ** | 各 ch を外部ディスクリ → OR ゲート IC → DIO に入れて `RP_TRIG_SRC_EXT_PE` を使う | 中（半田あり） | ✅ 真の OR | 最小 | ★★★★ |
| **B. 連続 DMA + ソフト判定** | DMA を `RP_TRIG_SRC_NOW` でリングバッファ的に回し、両 ch をソフトで閾値判定 | 中 | ✅（ただし読み出しレートに律速される） | 大 | ★★★ |
| **C. 交互トリガ** | ショットごとに `trigger_src` を `CHA_PE` ↔ `CHB_PE` で切替 | 小 | △ 統計的 OR | 通常通り | ★★ |
| **D. 閾値を低くして CH1 ハードトリガ + 解析側 OR フィルタ** | 現状コードで `trig_lvl` を下げ、`(max_ch1 > thr) | (max_ch2 > thr)` でカット | 最小 | ❌ CH1 ベースのバイアスが残る | 通常通り | ★（前段検証用に有効） |
| **E. カスタム FPGA ビットストリーム** | HDL を改修して `trig_src` に「CHA OR CHB」モードを追加 | 大（Vivado） | ✅ 真の OR | 最小 | ★（最終手段） |

**推奨順:** **A → B → C → E**（D は OR トリガの代替にはならないが診断には有効）。

---

### 5.A. 外部 OR ゲート + EXT トリガ（推奨度 ★★★★）

**仕組み:** 各チャンネルの解析信号を一旦コンパレータ／ディスクリミネータで TTL レベルのデジタル信号に変換し、74HC32（クアッド OR）などのゲート IC で OR を取って Red Pitaya の **外部トリガ入力（DIO0_N）** に入れます。Red Pitaya 側はトリガソースを `RP_TRIG_SRC_EXT_PE`（または `RP_TRIG_SRC_EXT_NE`）に設定するだけです。

```python
# cell-9 を以下に変更するだけ:
acq_trig_sour_list = [rp.RP_TRIG_SRC_EXT_PE]  # 立ち上がりエッジ
trigger_ch = 0
# rp_AcqSetTriggerLevel は EXT 入力にも有効ですが、TTL であれば 0.5 V 等で固定。
rp.rp_AcqSetTriggerLevel(rp.RP_T_CH_EXT, 0.5)
```

**長所:**
- 物理実験の標準的手法（NIM/CAMAC/VME ロジック構成と同じ思想）。
- ハードウェアレイテンシは数 ns 以下、デッドタイムは Red Pitaya 自身のサンプリング以下。
- どちらの ch がトリガを引いたかを **DIO の別ピンで記録** しておけば、解析時に「CH1 由来」「CH2 由来」「両方同時 = コインシデンス」も切り分けられる。

**短所:**
- 外部回路が必要。検出器がアナログ生波形を出している場合、ディスクリミネータが別途必要（既にディスクリ後の TTL を持っているなら 74HC32 一個で済む）。
- 閾値（ディスクリレベル）はソフトで変えられず、ハード側で設定する必要がある。

**注意点:**
- Red Pitaya の DIO 入力は **3.3 V 系**。TTL（5 V）から 3.3 V へのレベルシフトを忘れない。
- `rp.rp_SetSourceTrigOutput(rp.OUT_TR_ADC)` は **トリガ出力**（DIO0_N が出力モード）になります。EXT 入力で受け取る場合は別 DIO ピンを使うか、現在の `OUT_TR_ADC` 設定（cell-9）を無効化してください。

**結論:** 物理実験の本命解。配線さえできれば一番素直で性能も良い。

---

### 5.B. 連続 DMA + ソフトウェア判定（推奨度 ★★★）

**仕組み:** ハードウェアトリガを使わず、DMA を `RP_TRIG_SRC_NOW` で開始してリングバッファ的に DDR に書き続け、ソフトウェアが両 ch を周期的に読み出して閾値超えの位置を検出します。

```python
# 概念コード (本番投入には更に詰める必要あり)
rp.rp_AcqAxiSetTriggerDelay(rp.RP_CH_1, 0)
rp.rp_AcqAxiSetTriggerDelay(rp.RP_CH_2, 0)
rp.rp_AcqStart()
rp.rp_AcqSetTriggerSrc(rp.RP_TRIG_SRC_NOW)

while True:
    # DMA 領域から固定長ブロックを読む
    block_ch1, block_ch2 = read_dma_block()
    # 両 ch のどちらかが閾値超え？
    over = (block_ch1.max() > thr) or (block_ch2.max() > thr)
    if over:
        # 該当ショットを記録、もしくは前後窓を切り出して保存
        save(block_ch1, block_ch2)
```

**長所:**
- 真の OR が実現できる。ソフトで `(max_ch1 > thr) | (max_ch2 > thr)` を AND/OR 何でも組める。
- ハードウェア改造不要。

**短所:**
- **デッドタイムが大きい**。125 MSPS × 2 byte × 2 ch = 500 MB/s。Red Pitaya の DMA 予約領域は通常 ~128 MB なので、**フルレートでは ~0.25 秒分しか溜められない**。サンプリングレートを下げる（`rp.RP_DEC_8` 以上）か、ブロック単位で読み出して判定する形になる。
- 検出器イベントレートが Red Pitaya の DMA→Linux 読み出し帯域を超えると取りこぼし。
- ソフト側のレイテンシが長く、放射線検出など μs 級の応答性が必要な用途には不向き。

**結論:** イベントレートが低い・サンプリングレートを下げて良い場合に有効。**ハードを触りたくないなら次善の策。**

---

### 5.C. 交互トリガ（推奨度 ★★）

**仕組み:** 1 ショット目は `CHA_PE`、2 ショット目は `CHB_PE`、… と交互に切り替える。

```python
def alternating_trigger():
    yield_idx = 0
    sources = [rp.RP_TRIG_SRC_CHA_PE, rp.RP_TRIG_SRC_CHB_PE]
    while True:
        yield sources[yield_idx % 2]
        yield_idx += 1

trig_iter = alternating_trigger()
while True:
    waveform_arr = acqADC_DMA(next(trig_iter))
    ...
```

**長所:**
- 実装が一番簡単。10 行未満の追加で済む。
- ハードウェア改造ゼロ。

**短所:**
- **イベントを統計的に半分しか取得できない**。CH1 トリガ待ちの間に CH2 だけにイベントが来てもデッドタイム中なら見逃す（逆も同様）。本物の OR とは似て非なるもの。
- イベントレートがハード／ソフトのデッドタイム時間スケールより十分低ければ実用的。

**結論:** 「ハード触りたくない、簡単に試したい、イベントレートは低い」というケースで有効。

---

### 5.D. 閾値を低くして CH1 ハードトリガ + 解析側 OR フィルタ（前段の診断用、★）

**仕組み:** 現状のままハードトリガは CH1 にかけたまま、`trig_lvl` をベースラインノイズすれすれまで下げる。両 ch の波形は DMA で全部記録されているので、解析時に `(max_ch1 > thr) | (max_ch2 > thr)` で OR フィルタ。

**注意:** これは **真の OR トリガではありません**。

- ハードウェアは依然として **CH1 だけ** をモニタしているので、**CH2 単独イベント（CH1 はベースライン以下）はそもそも記録されない**。
- 「CH1 ベースの取得 → 解析側で CH2 もカット」という構造になり、CH1 にバイアスが残る。

**有効に使える場面:**
- まずこの方法で計測して **「CH1 で見えていて CH2 でも見えている事象」と「CH1 のみ事象」の比率** を出す。
- 次に CH2 を hardware trigger にして同じ閾値で計測し、**「CH2 のみ事象」がどれくらい出るか** を見る。
- これらから **本物の OR トリガを導入したときに何倍のイベントレートになるか** を見積もれる。
- 本物の OR トリガ（A or B）を入れる **前のフィージビリティ確認**として使う。

#### 既存ノートブックでの実装例（解析側 1 行追加）

`detector-analysis-dma.ipynb` の §3 直後にコインシデンス／OR フィルタを足すだけ:

```python
thr = 0.05  # [V]
peak1 = ch1.max(axis=1)
peak2 = ch2.max(axis=1)
mask_or    = (peak1 > thr) | (peak2 > thr)   # OR
mask_and   = (peak1 > thr) & (peak2 > thr)   # AND (コインシデンス)
mask_xor   = mask_or & ~mask_and              # 片 ch のみ
print(f"OR={mask_or.sum()}, AND={mask_and.sum()}, XOR={mask_xor.sum()} / {n_shots}")
```

---

### 5.E. カスタム FPGA ビットストリーム（最終手段、★）

Red Pitaya のソース（`RedPitaya/RedPitaya` リポジトリ）の `fpga/rtl/red_pitaya_acq_trig.v` 系（ファイル名は版次第）にトリガ条件の HDL があります。CHA トリガ条件信号と CHB トリガ条件信号は内部に存在し、それを `if` で選択する形になっています。これを `chA_trig | chB_trig` に書き換えて新しいトリガモードを追加すれば、真の OR が「もう 1 つのトリガソース」として API レベルで使えるようになります。

**長所:** 真の OR、低レイテンシ、低デッドタイム、ソフト側は定数 1 つ追加するだけ。

**短所:**
- Vivado（無償版で OK）でビルド環境を構築する必要がある。
- FPGA イメージを差し替えるリスク（標準機能が壊れていないかリグレッションテストが必要）。
- バージョンアップで標準ビットストリームに追従するメンテが発生する。

**結論:** 案 A / B が要件を満たすなら手を出す必要なし。研究室で長期に複数の検出器構成を扱うなら投資する価値あり。

---

### おすすめのロードマップ

1. **まず案 D で現状把握**: 5.D の解析側スクリプトを `detector-analysis-dma.ipynb` に 5 行追加し、CH1 ハードトリガ下で「CH1 のみ事象 vs 両 ch 事象」の比率を確認。CH2 だけのイベントはここでは見えないが、両 ch の比率と過去の物理予測から「OR にすると何倍イベントが増えそうか」を見積もる。
2. **次に案 C を試す（30 分で実装可能）**: 5.C のラッパで CHA/CHB を交互にトリガ。`trigger_src` ごとにイベントレートを別カウントすれば、CH2 単独事象もある程度検出できる。これで「真の OR が必要かどうか」が定量的に判断できる。
3. **本格運用が必要になったら案 A**: ディスクリ + 74HC32 で OR を組み、`RP_TRIG_SRC_EXT_PE`。これが物理実験の標準解。
4. **イベントレートが低くハードを増やせない場合は案 B**: 連続 DMA + ソフト判定。`rp_AcqAxiSetTriggerDelay = 0` + `RP_TRIG_SRC_NOW` で実装。
5. **頻繁に複雑な条件（コインシデンス、マルチチャンネル AND/OR）を切り替えたいなら案 E**: HDL 改造で複数トリガモード追加。

---

## 6. 全体としての次にやることリスト（優先度順）

### 対応済み

- ✅ **H2**: `acqADC` / `acqADC_DMA` から `rp_GenTriggerOnly()` を削除（OUT1 不使用）。
- ✅ **M1**: cell-9 冒頭に `# === 設定 (Config) ===` セクションを設けてパラメータ集中管理。
- ✅ **M2**: `TRIG_SRC_NAMES` 逆引き辞書を導入し、メタの `trigger_src` を派生化。
- ✅ **M3**: `INPUT_JUMPER` + `LSB_VOLTS_BY_JUMPER` で LV/HV 切替を 1 行化。
- ✅ **M4**: 共通ヘルパ `_start_and_wait()` を抽出して `acqADC` / `acqADC_DMA` で共用。
- ✅ **M5**: `time.sleep(0.001)` を `_START_SETTLE_S` 定数化、意図を日本語コメントで明示。

### 対応見送り（運用判断）

- ❎ **H1**: トリガ待ちのタイムアウト導入は**実施しない**。トリガが来るまでの想定時間が状況依存のため、ハングしたら手動 `KeyboardInterrupt` で止める運用。

### 未対応（次以降の課題）

1. **L10/L11**: `paper_style.py` の docstring の `25:9` → `16:9` 修正、`figsize` の数値例を 1280 px に合わせる。
2. **OR トリガ案 D**: `detector-analysis-dma.ipynb` に 5 行追加して両 ch の閾値超え事象の比率をまず確認。
3. **M6〜M8**: 解析側のベースライン引きをプリトリガ平均に変更、相互相関を FFT 法に切替、`parabolic_peak_offset` の 0 戻り → NaN。
4. **OR トリガ案 A の物理構成検討**: 検出器の出力レベルとディスクリ閾値、74HC32 の電源、Red Pitaya DIO のレベルシフト。
5. **M9/M10**: `np.genfromtxt` → `np.loadtxt` または `.npz` 化、ビン幅自動算出を Freedman-Diaconis に。

---

ご質問の OR トリガについては、**結論: 直接の API はないが現実的な選択肢が複数ある**。中でも **案 A（外部 OR ゲート + EXT トリガ）** が物理実験での本命、**案 B（連続 DMA + ソフト判定）** が次善、**案 C（交互トリガ）** は試行コストが最小、という整理になります。**案 D は OR トリガそのものではなく、本物の OR トリガ導入前のフィージビリティ確認に使える** という位置づけです。
