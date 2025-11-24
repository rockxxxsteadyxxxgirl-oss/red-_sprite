import json
import math
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from urllib import error, request

import tkintermapview as tkmv


def clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def trapezoid_score(value: float, low: float, opt_low: float, opt_high: float, high: float) -> float:
    """
    線形台形で0-1スコアを返す。low〜opt_lowで線形増、opt_high〜highで線形減、最適区間は1。
    """
    if value <= low or value >= high:
        return 0.0
    if opt_low <= value <= opt_high:
        return 1.0
    if value < opt_low:
        return clamp((value - low) / (opt_low - low))
    return clamp((high - value) / (high - opt_high))


def predict_red_sprite_probability(
    latitude: float,
    longitude: float,
    month: int,
    hour: int,
    storm_activity: float,
    cloud_cover: float,
    moon_brightness: float,
    visibility_km: float,
) -> tuple[float, list[str], str]:
    """
    ロジスティック結合を使った簡易ヒューリスティック推定。
    0-1の確率、理由リスト、日本語ヒントを返す。
    """
    reasons: list[str] = []

    lat_score = trapezoid_score(latitude, low=-10.0, opt_low=10.0, opt_high=45.0, high=60.0)
    month_score = trapezoid_score(month, low=2.5, opt_low=5.0, opt_high=9.0, high=11.5)
    if 21 <= hour <= 23 or 0 <= hour <= 2:
        hour_score = 1.0
    elif 18 <= hour <= 20 or 3 <= hour <= 5:
        hour_score = 0.6
    else:
        hour_score = 0.1
    storm_factor = clamp(storm_activity / 10.0)
    cloud_clear = clamp(1.0 - (cloud_cover / 100.0))
    moon_dark = clamp(1.0 - (moon_brightness / 100.0))
    visibility_factor = clamp(visibility_km / 40.0)

    z = (
        -3.0
        + 0.6 * lat_score
        + 0.5 * month_score
        + 0.4 * hour_score
        + 2.0 * storm_factor
        + 0.6 * visibility_factor
        + 0.4 * cloud_clear
        + 0.2 * moon_dark
    )
    probability = 1.0 / (1.0 + math.exp(-z))

    if lat_score >= 0.9:
        reasons.append("緯度は最適帯（10-45度）で有利。")
    elif lat_score >= 0.5:
        reasons.append("緯度は許容帯（〜60度）でやや有利。")
    else:
        reasons.append("緯度が典型帯から外れ、寄与が低い。")

    if month_score >= 0.9:
        reasons.append("季節は暖候期（5-9月）で対流活動が強まりやすい。")
    elif month_score >= 0.4:
        reasons.append("季節は肩シーズンで中程度の寄与。")
    else:
        reasons.append("寒候期で季節寄与が弱い。")

    if hour_score == 1.0:
        reasons.append("夜間（21-02時）で観測しやすい。")
    elif hour_score == 0.6:
        reasons.append("薄暮/明け方で観測可能性は中程度。")
    else:
        reasons.append("日中帯で観測困難。")

    if storm_factor > 0.7:
        reasons.append("雷活動が非常に活発。")
    elif storm_factor > 0.4:
        reasons.append("雷活動は中程度。")
    else:
        reasons.append("雷活動が弱く誘発しづらい。")

    if cloud_clear > 0.6:
        reasons.append("雲が少なく視程を阻害しない。")
    elif cloud_clear > 0.3:
        reasons.append("雲がやや多めで減衰あり。")
    else:
        reasons.append("雲が多く上空が遮られている。")

    if moon_dark > 0.7:
        reasons.append("月明かりが弱く空が暗い。")
    elif moon_dark > 0.3:
        reasons.append("月明かりは中程度。")
    else:
        reasons.append("月明かりが強く暗順応しづらい。")

    if visibility_factor >= 0.5:
        reasons.append("視程良好。")
    else:
        reasons.append("視程が短く減光が大きい。")

    if probability > 0.7:
        hint = "観測条件は良好です。カメラと双眼鏡を準備し、雷雲の真上より少し離れた方向を注視。"
    elif probability > 0.4:
        hint = "条件は並程度。落雷数が増えれば狙い目です。カメラは長秒露光を準備。"
    else:
        hint = "条件は弱め。雷活動が活発化するタイミングまで待機がおすすめ。"

    return probability, reasons, hint


def moon_illumination(dt: datetime) -> float:
    """
    簡易な月の照度（照らされている割合）を0-1で返す。天文精度は高くない簡易計算。
    """
    year = dt.year
    month = dt.month
    day = dt.day + (dt.hour / 24.0)
    if month < 3:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + (a // 4)
    jd = int(365.25 * (year + 4716)) + int(30.6001 * (month + 1)) + day + b - 1524.5
    days_since_new = jd - 2451549.5
    synodic_month = 29.53058867
    phase = (days_since_new % synodic_month) / synodic_month
    illumination = (1 - math.cos(2 * math.pi * phase)) / 2
    return clamp(illumination, 0.0, 1.0)


class SpriteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("レッドスプライト観測予測")
        self.geometry("1120x720")
        self.resizable(False, False)
        self._default_latlon = (35.0, 138.0)
        self.marker = None
        self._build_ui()

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)
        main.grid_columnconfigure(0, weight=1, minsize=380)
        main.grid_columnconfigure(1, weight=1)
        main.grid_rowconfigure(0, weight=1)

        left = ttk.Frame(main, padding=(0, 0, 10, 0))
        left.grid(row=0, column=0, sticky="nsew")
        right = ttk.Frame(main)
        right.grid(row=0, column=1, sticky="nsew")

        form = ttk.LabelFrame(left, text="観測条件の入力", padding=10)
        form.pack(fill=tk.X)

        row1 = ttk.Frame(form)
        row1.pack(fill=tk.X, pady=4)
        ttk.Label(row1, text="緯度 (-90〜90)").pack(side=tk.LEFT, padx=(0, 6))
        self.lat_entry = ttk.Entry(row1, width=10)
        self.lat_entry.insert(0, f"{self._default_latlon[0]:.2f}")
        self.lat_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row1, text="経度 (-180〜180)").pack(side=tk.LEFT, padx=(0, 6))
        self.lon_entry = ttk.Entry(row1, width=10)
        self.lon_entry.insert(0, f"{self._default_latlon[1]:.2f}")
        self.lon_entry.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row1, text="月 (1-12)").pack(side=tk.LEFT, padx=(0, 6))
        self.month_spin = tk.Spinbox(row1, from_=1, to=12, width=5)
        self.month_spin.delete(0, tk.END)
        self.month_spin.insert(0, datetime.now().month)
        self.month_spin.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row1, text="時刻 (0-23)").pack(side=tk.LEFT, padx=(0, 6))
        self.hour_spin = tk.Spinbox(row1, from_=0, to=23, width=5)
        self.hour_spin.delete(0, tk.END)
        self.hour_spin.insert(0, datetime.now().hour)
        self.hour_spin.pack(side=tk.LEFT)

        row2 = ttk.Frame(form)
        row2.pack(fill=tk.X, pady=4)
        label_row = ttk.Frame(row2)
        label_row.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(label_row, text="雷活動（0:静穏〜10:非常に活発）").pack(side=tk.LEFT)
        ttk.Button(label_row, text="目安", width=6, command=self.show_storm_help).pack(side=tk.LEFT, padx=(6, 0))
        self.storm_scale = tk.Scale(row2, from_=0, to=10, orient=tk.HORIZONTAL, resolution=0.5, length=260)
        self.storm_scale.set(6)
        self.storm_scale.pack(anchor=tk.W)

        row3 = ttk.Frame(form)
        row3.pack(fill=tk.X, pady=4)
        cloud_label_row = ttk.Frame(row3)
        cloud_label_row.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(cloud_label_row, text="雲量％").pack(side=tk.LEFT)
        ttk.Button(cloud_label_row, text="目安", width=6, command=self.show_cloud_help).pack(side=tk.LEFT, padx=(6, 0))
        self.cloud_scale = tk.Scale(row3, from_=0, to=100, orient=tk.HORIZONTAL, resolution=5, length=260)
        self.cloud_scale.set(30)
        self.cloud_scale.pack(anchor=tk.W)

        row4 = ttk.Frame(form)
        row4.pack(fill=tk.X, pady=4)
        moon_label_row = ttk.Frame(row4)
        moon_label_row.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(moon_label_row, text="月明かりの明るさ％").pack(side=tk.LEFT)
        ttk.Button(moon_label_row, text="目安", width=6, command=self.show_moon_help).pack(side=tk.LEFT, padx=(6, 0))
        self.moon_scale = tk.Scale(row4, from_=0, to=100, orient=tk.HORIZONTAL, resolution=5, length=260)
        self.moon_scale.set(40)
        self.moon_scale.pack(anchor=tk.W)

        row5 = ttk.Frame(form)
        row5.pack(fill=tk.X, pady=4)
        visibility_label_row = ttk.Frame(row5)
        visibility_label_row.pack(anchor=tk.W, fill=tk.X)
        ttk.Label(visibility_label_row, text="視程 (km) 0-40").pack(side=tk.LEFT)
        ttk.Button(visibility_label_row, text="目安", width=6, command=self.show_visibility_help).pack(side=tk.LEFT, padx=(6, 0))
        self.visibility_scale = tk.Scale(row5, from_=0, to=40, orient=tk.HORIZONTAL, resolution=1, length=260)
        self.visibility_scale.set(20)
        self.visibility_scale.pack(anchor=tk.W)

        button_row = ttk.Frame(form)
        button_row.pack(fill=tk.X, pady=8)
        ttk.Button(button_row, text="予測する", command=self.run_prediction).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(button_row, text="入力値の地点へ地図移動", command=self.move_map_to_entries).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Button(button_row, text="APIで自動取得", command=self.auto_fetch_conditions).pack(side=tk.LEFT, padx=(12, 0))

        result_box = ttk.LabelFrame(left, text="予測結果", padding=10)
        result_box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        prob_row = ttk.Frame(result_box)
        prob_row.pack(fill=tk.X)
        ttk.Label(prob_row, text="出現確率推定").pack(side=tk.LEFT, padx=(0, 8))
        self.prob_var = tk.DoubleVar(value=0.0)
        self.prog = ttk.Progressbar(prob_row, maximum=100, variable=self.prob_var, length=280)
        self.prog.pack(side=tk.LEFT, padx=(0, 12))
        self.prob_label = ttk.Label(prob_row, text="0 %")
        self.prob_label.pack(side=tk.LEFT)

        self.hint_label = ttk.Label(result_box, text="条件を入力して「予測する」を押してください。", wraplength=700, padding=(0, 6))
        self.hint_label.pack(anchor=tk.W)

        ttk.Label(result_box, text="理由").pack(anchor=tk.W)
        self.reason_box = tk.Text(result_box, height=10, wrap=tk.WORD)
        self.reason_box.insert(tk.END, "入力待ち…")
        self.reason_box.configure(state=tk.DISABLED)
        self.reason_box.pack(fill=tk.BOTH, expand=True, pady=(2, 0))
        foot_row = ttk.Frame(result_box)
        foot_row.pack(anchor=tk.W, pady=(6, 0))
        ttk.Button(foot_row, text="計算方式を表示", command=self.show_formula).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(foot_row, text="デモ条件を読み込む", command=self.load_demo).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(foot_row, text="理想条件を見る", command=self.show_best_conditions).pack(side=tk.LEFT)

        map_box = ttk.LabelFrame(right, text="地図で地点を選択", padding=8)
        map_box.pack(fill=tk.BOTH, expand=True)

        info_row = ttk.Frame(map_box)
        info_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(info_row, text="地図をクリックすると緯度・経度に反映されます。").pack(side=tk.LEFT)

        self.map_widget = tkmv.TkinterMapView(map_box, width=480, height=620, corner_radius=0)
        self.map_widget.pack(fill=tk.BOTH, expand=True)
        self.map_widget.set_tile_server("https://a.tile.openstreetmap.org/{z}/{x}/{y}.png")
        self.map_widget.set_zoom(5)
        self.map_widget.set_position(*self._default_latlon)
        self.map_widget.add_left_click_map_command(self.on_map_click)
        self.update_marker(*self._default_latlon, move=False)

    def load_demo(self) -> None:
        self.lat_entry.delete(0, tk.END)
        self.lat_entry.insert(0, "34.7")
        self.lon_entry.delete(0, tk.END)
        self.lon_entry.insert(0, "136.5")
        self.month_spin.delete(0, tk.END)
        self.month_spin.insert(0, "7")
        self.hour_spin.delete(0, tk.END)
        self.hour_spin.insert(0, "22")
        self.storm_scale.set(8.5)
        self.cloud_scale.set(20)
        self.moon_scale.set(20)
        self.visibility_scale.set(30)
        self.move_map_to_entries(center_only=True)
        self.run_prediction()

    def on_map_click(self, coords: tuple[float, float]) -> None:
        lat, lon = coords
        self.lat_entry.delete(0, tk.END)
        self.lat_entry.insert(0, f"{lat:.4f}")
        self.lon_entry.delete(0, tk.END)
        self.lon_entry.insert(0, f"{lon:.4f}")
        self.update_marker(lat, lon, move=False)

    def move_map_to_entries(self, center_only: bool = False) -> None:
        try:
            lat = float(self.lat_entry.get())
            lon = float(self.lon_entry.get())
        except ValueError:
            messagebox.showerror("入力エラー", "緯度・経度を正しく入力してください。")
            return
        self.map_widget.set_position(lat, lon)
        if not center_only:
            self.update_marker(lat, lon, move=False)

    def update_marker(self, lat: float, lon: float, move: bool = False) -> None:
        if move:
            self.map_widget.set_position(lat, lon)
        if self.marker:
            self.marker.delete()
        self.marker = self.map_widget.set_marker(lat, lon, text=f"{lat:.2f}, {lon:.2f}")

    def run_prediction(self) -> None:
        try:
            lat = float(self.lat_entry.get())
            lon = float(self.lon_entry.get())
            month = int(self.month_spin.get())
            hour = int(self.hour_spin.get())
        except ValueError:
            messagebox.showerror("入力エラー", "数値を正しく入力してください。")
            return

        try:
            prob, reasons, hint = predict_red_sprite_probability(
                latitude=lat,
                longitude=lon,
                month=month,
                hour=hour,
                storm_activity=float(self.storm_scale.get()),
                cloud_cover=float(self.cloud_scale.get()),
                moon_brightness=float(self.moon_scale.get()),
                visibility_km=float(self.visibility_scale.get()),
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("計算エラー", f"計算に失敗しました: {exc}")
            return

        percent = round(prob * 100)
        self.prob_var.set(percent)
        self.prob_label.configure(text=f"{percent} %")
        self.hint_label.configure(text=hint)

        detail_lines = [f"緯度: {lat:.2f}°, 経度: {lon:.2f}°", "------"]
        detail_lines.extend(f"・{r}" for r in reasons)
        self.reason_box.configure(state=tk.NORMAL)
        self.reason_box.delete("1.0", tk.END)
        self.reason_box.insert(tk.END, "\n".join(detail_lines))
        self.reason_box.configure(state=tk.DISABLED)
        self.update_marker(lat, lon, move=False)

    def auto_fetch_conditions(self) -> None:
        try:
            lat = float(self.lat_entry.get())
            lon = float(self.lon_entry.get())
            hour = int(self.hour_spin.get())
        except ValueError:
            messagebox.showerror("入力エラー", "緯度・経度・時刻を正しく入力してください。")
            return

        target_dt = datetime.now().replace(minute=0, second=0, microsecond=0)
        target_dt = target_dt.replace(hour=hour)

        try:
            cloud, visibility = self.fetch_weather(lat, lon, target_dt)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("取得失敗", f"天気APIの取得に失敗しました: {exc}")
            return

        moon_pct = round(moon_illumination(target_dt) * 100)
        moon_pct = clamp(moon_pct, 0, 100)

        self.cloud_scale.set(round(cloud))
        self.visibility_scale.set(round(visibility))
        self.moon_scale.set(round(moon_pct))
        messagebox.showinfo(
            "自動取得完了",
            "Open-Meteoから雲量・視程を取得し、月明かりは現在日付+指定時刻で計算しました。\n"
            f"雲量: {cloud:.1f}%, 視程: {visibility:.1f} km, 月明かり推定: {moon_pct:.0f}%",
        )
        # 自動取得後に最新値で再計算
        self.run_prediction()

    def fetch_weather(self, lat: float, lon: float, target_dt: datetime) -> tuple[float, float]:
        """
        Open-Meteoから雲量(%)と視程(km)を取得し、指定時刻に最も近い値を返す。
        """
        base_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&hourly=cloudcover,visibility"
            "&past_days=1&forecast_days=1&timezone=auto"
        )
        try:
            with request.urlopen(base_url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"通信エラー: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("レスポンスの解析に失敗しました") from exc

        hourly = data.get("hourly")
        if not hourly or "time" not in hourly:
            raise RuntimeError("時間別データが見つかりませんでした")

        times = hourly["time"]
        clouds = hourly.get("cloudcover", [])
        visibilities = hourly.get("visibility", [])
        target_key = target_dt.strftime("%Y-%m-%dT%H:00")

        def nearest_index() -> int:
            for idx, t in enumerate(times):
                if t.startswith(target_key):
                    return idx
            target_no_tz = datetime.strptime(target_key, "%Y-%m-%dT%H:00")
            deltas = [abs(datetime.fromisoformat(t) - target_no_tz) for t in times]
            return deltas.index(min(deltas))

        idx = nearest_index()
        try:
            cloud_val = float(clouds[idx])
            vis_val_km = float(visibilities[idx]) / 1000.0
        except (IndexError, ValueError) as exc:
            raise RuntimeError("データ抽出に失敗しました") from exc
        return clamp(cloud_val, 0, 100), clamp(vis_val_km, 0, 40)

    def show_formula(self) -> None:
        lines = [
            "現在の計算方法（ロジスティック結合ヒューリスティック）:",
            "- 台形スコアで0〜1に正規化してから重み付けし z を計算",
            "  緯度: low=-10, 最適=10〜45, high=60 → 重み0.6",
            "  季節: low=2.5, 最適=5〜9, high=11.5 → 重み0.5",
            "  時刻: 21-02時=1.0, 18-20/3-5時=0.6, それ以外=0.1 → 重み0.4",
            "  雷活動: (0〜10)/10 → 重み2.0",
            "  雲量: (1 - 雲量%/100) → 重み0.4",
            "  月明かり: (1 - 明るさ%/100) → 重み0.2",
            "  視程: (km/40) → 重み0.6",
            "- z = -3.0 + Σ(重み×スコア)、確率 = 1/(1+exp(-z)) を0〜100%表示",
            "- 70%以上: 良好, 40%以上: 並, それ未満: 弱め のヒント",
        ]
        messagebox.showinfo("計算方式", "\n".join(lines))

    def show_storm_help(self) -> None:
        lines = [
            "雷活動(0〜10)の入れ方の目安:",
            "- 気象庁 雷ナウキャスト: 色付き発雷域が連続=6〜8, 広域で強=9〜10, 点在=3〜5, 無=0",
            "- 落雷回数(直近1h): 0回=0, 1-3回=2〜3, 4-10回=5, 11-30回=8, 30回超=9〜10",
            "- レーダー強エコー: 35-45dBZ孤立セル=2〜5, 45-55dBZのセル群=6〜8, 55dBZ超の多セル/線状=9〜10",
            "- 手元の雷センサー(10分平均): 0回=0, 1-2回=3, 3-5回=5, 6-10回=7, 10回超=9〜10",
            "- 体感: 遠雷がたまに=3〜4, 10分に数回鳴る=5〜6, ほぼ鳴り続く=8〜10",
        ]
        messagebox.showinfo("雷活動の目安", "\n".join(lines))

    def show_cloud_help(self) -> None:
        lines = [
            "雲量(0〜100%)の入れ方の目安:",
            "- 気象衛星画像（赤外/可視）: 広域に厚い雲=80〜100, 積雲が帯状/まとまる=40〜70, ほぼ雲なし=0〜20",
            "- 雲量オクタ(METAR/TAF): 0/8=0, 1/8=12, 2/8=25, 3/8=37, 4/8=50, 5/8=62, 6/8=75, 7/8=87, 8/8=100 に換算",
            "- 直感: 空の7〜10割が雲=70〜100, 4〜6割=40〜60, 1〜3割=10〜30, ほぼ快晴=0〜5",
            "- レーダーや衛星の薄雲判別が難しい時は、星がうっすら見える=20〜40, 星がほぼ見えない=60〜90で入力",
        ]
        messagebox.showinfo("雲量の目安", "\n".join(lines))

    def show_moon_help(self) -> None:
        lines = [
            "月明かりの明るさ(0〜100%)の入れ方の目安:",
            "- 月齢: 新月〜三日月=0〜20, 上弦/下弦=40〜60, 十三夜〜満月=80〜100",
            "- 月高度: 地平線近くは20〜40%に抑え、高く昇るほど＋20〜40%を上乗せ",
            "- 雲越し: 薄雲で朧月=20〜40, 厚めの雲でボヤける=40〜70, くっきり見える=70〜100",
            "- 簡易推定: 月が見えない=0〜10, 輪郭がぼんやり=30〜50, 眩しい/影ができる=70〜100",
        ]
        messagebox.showinfo("月明かりの目安", "\n".join(lines))

    def show_visibility_help(self) -> None:
        lines = [
            "視程(0〜40km)の入れ方の目安:",
            "- 気象台/空港METAR: VIS 10km以上→10〜15km、9999表記なら15km以上とみなす",
            "- 地物で推定: 近くの山やランドマークの距離を既知とし、見え方に応じて 5km, 10km, 20km などを入力",
            "- 星空の見え方: 夏の天の川がはっきり=15〜25km, ぼんやり=8〜15km, ほぼ見えない=5km以下",
            "- 霧/黄砂/煙霧: 霞んで輪郭が不明瞭=2〜5km, 建物の形が崩れる=1〜2km, すぐ近くしか見えない=0〜1km",
            "- 不明なときの簡易: 遠くの高層ビルが見える=10〜15km, 山稜が見える=15〜25km, ほぼ見えない=0〜5km",
        ]
        messagebox.showinfo("視程の目安", "\n".join(lines))

    def show_best_conditions(self) -> None:
        lines = [
            "理想に近い観測条件の目安:",
            "- 場所: 緯度10〜45度帯。都市光害が少ない開けた場所。雷雲から水平距離50〜150km離れて側方〜背後を狙う。",
            "- 季節/時間: 暖候期(5〜9月)。時刻は21〜02時が最有利、18〜20時/3〜5時が次点。",
            "- 気象: 雷活動が非常に活発(落雷多いセル/強エコー)。雲量20%以下。視程20km以上。降水域の真下は避ける。",
            "- 光条件: 新月〜三日月や月が低い/陰るタイミング。街灯や車灯が少ない暗所で暗順応。",
            "- 観測姿勢: 雷雲の真上ではなく少し離れた上空を注視。広角・長秒露光+三脚、連写/インターバル撮影で記録。",
        ]
        messagebox.showinfo("理想条件", "\n".join(lines))


if __name__ == "__main__":
    app = SpriteApp()
    app.mainloop()
