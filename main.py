import json
import math
from datetime import datetime
from typing import Any
from urllib import error, request

import folium
import streamlit as st
from streamlit_folium import st_folium


st.set_page_config(page_title="レッドスプライト観測予測(Web)", layout="wide")


def clamp(value: float, min_value: float = 0.0, max_value: float = 1.0) -> float:
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def trapezoid_score(value: float, low: float, opt_low: float, opt_high: float, high: float) -> float:
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
        hint = "観測条件は良好。雷雲の真上より少し離れた方向を注視し、カメラと双眼鏡を準備。"
    elif probability > 0.4:
        hint = "条件は並程度。落雷数が増えれば狙い目。カメラは長秒露光を準備。"
    else:
        hint = "条件は弱め。雷活動が活発化するタイミングまで待機がおすすめ。"

    return probability, reasons, hint


def moon_illumination(dt: datetime) -> float:
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


def fetch_weather(lat: float, lon: float, target_dt: datetime) -> tuple[float, float]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=cloudcover,visibility"
        "&past_days=1&forecast_days=1&timezone=auto"
    )
    try:
        with request.urlopen(url, timeout=10) as resp:
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


def init_state() -> None:
    defaults = {
        "lat": 35.0,
        "lon": 138.0,
        "month": datetime.now().month,
        "hour": datetime.now().hour,
        "storm": 6.0,
        "cloud": 30.0,
        "moon": 40.0,
        "vis": 20.0,
    }
    for k, v in defaults.items():
        st.session_state.setdefault(k, v)


def build_map(lat: float, lon: float) -> dict[str, Any] | None:
    m = folium.Map(location=[lat, lon], zoom_start=5, control_scale=True)
    folium.Marker([lat, lon], tooltip=f"{lat:.3f}, {lon:.3f}").add_to(m)
    return st_folium(m, width=520, height=480, key="map")


def render_inputs() -> None:
    st.subheader("観測条件の入力")
    col1, col2 = st.columns([1, 1])
    with col1:
        st.number_input("緯度 (-90〜90)", value=st.session_state["lat"], key="lat", step=0.1)
        st.number_input("経度 (-180〜180)", value=st.session_state["lon"], key="lon", step=0.1)
        st.number_input("月 (1-12)", min_value=1, max_value=12, value=st.session_state["month"], key="month")
        st.number_input("時刻 (0-23)", min_value=0, max_value=23, value=st.session_state["hour"], key="hour")
        st.slider("雷活動（0:静穏〜10:非常に活発）", 0.0, 10.0, value=st.session_state["storm"], step=0.5, key="storm")
        with st.expander("雷活動の目安"):
            st.write(
                "- 雷ナウキャスト: 色付き発雷域が連続=6〜8, 広域で強=9〜10, 点在=3〜5, 無=0\n"
                "- 落雷回数(直近1h): 0=0, 1-3=2〜3, 4-10=5, 11-30=8, 30+ =9〜10\n"
                "- レーダー強エコー: 35-45dBZ孤立=2〜5, 45-55dBZ群=6〜8, 55dBZ超=9〜10\n"
                "- 雷検知器10分平均: 0=0, 1-2=3, 3-5=5, 6-10=7, 10+ =9〜10\n"
                "- 体感: 遠雷たまに=3〜4, 10分に数回=5〜6, ほぼ鳴り続く=8〜10"
            )
    with col2:
        st.slider("雲量％", 0.0, 100.0, value=st.session_state["cloud"], step=5.0, key="cloud")
        with st.expander("雲量の目安"):
            st.write(
                "- 衛星画像: 厚い雲=80〜100, 積雲帯/まとまり=40〜70, ほぼ雲なし=0〜20\n"
                "- オクタ換算: 0/8=0,1/8=12,2/8=25,3/8=37,4/8=50,5/8=62,6/8=75,7/8=87,8/8=100\n"
                "- 目視: 空の雲が7〜10割=70〜100, 4〜6割=40〜60, 1〜3割=10〜30, 快晴=0〜5\n"
                "- 星の見え方: うっすら見える=20〜40, ほぼ見えない=60〜90"
            )
        st.slider("月明かりの明るさ％", 0.0, 100.0, value=st.session_state["moon"], step=5.0, key="moon")
        with st.expander("月明かりの目安"):
            st.write(
                "- 月齢: 新月〜三日月=0〜20, 上弦/下弦=40〜60, 十三夜〜満月=80〜100\n"
                "- 月高度: 低い=20〜40%, 高いほど+20〜40%\n"
                "- 雲越し: 朧月=20〜40, 厚めの雲でボヤける=40〜70, くっきり=70〜100\n"
                "- 体感: 見えない=0〜10, ぼんやり=30〜50, 眩しく影=70〜100"
            )
        st.slider("視程 (km) 0-40", 0.0, 40.0, value=st.session_state["vis"], step=1.0, key="vis")
        with st.expander("視程の目安"):
            st.write(
                "- METAR VIS: 10km+ →10〜15km、9999なら15km以上\n"
                "- 地物: 山/ランドマークの距離で 5/10/20km など\n"
                "- 星空: 天の川くっきり=15〜25km, ぼんやり=8〜15km, 見えない=〜5km\n"
                "- 霧/黄砂: 輪郭不明=2〜5km, 形が崩れる=1〜2km, 直近のみ=0〜1km"
            )


def render_map() -> None:
    st.subheader("地図で地点を選択（クリックで緯度経度に反映）")
    map_data = build_map(st.session_state["lat"], st.session_state["lon"])
    if map_data and map_data.get("last_clicked"):
        lc = map_data["last_clicked"]
        st.session_state["lat"] = lc["lat"]
        st.session_state["lon"] = lc["lng"]
        st.info(f"地図から設定: 緯度 {lc['lat']:.4f}, 経度 {lc['lng']:.4f}")


def render_actions() -> None:
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("予測する", type="primary"):
            run_prediction_and_show()
    with col2:
        if st.button("APIで自動取得（雲量・視程・月明かり）"):
            auto_fetch()
    with col3:
        if st.button("理想条件を見る"):
            show_best_conditions()


def run_prediction_and_show() -> None:
    try:
        prob, reasons, hint = predict_red_sprite_probability(
            latitude=float(st.session_state["lat"]),
            longitude=float(st.session_state["lon"]),
            month=int(st.session_state["month"]),
            hour=int(st.session_state["hour"]),
            storm_activity=float(st.session_state["storm"]),
            cloud_cover=float(st.session_state["cloud"]),
            moon_brightness=float(st.session_state["moon"]),
            visibility_km=float(st.session_state["vis"]),
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"計算に失敗しました: {exc}")
        return

    percent = round(prob * 100)
    st.subheader("予測結果")
    st.progress(prob, text=f"{percent}%")
    st.metric("出現確率 (推定)", f"{percent} %")
    st.write(f"**観測ヒント:** {hint}")
    st.write("**理由**")
    for r in reasons:
        st.write(f"- {r}")


def auto_fetch() -> None:
    try:
        lat = float(st.session_state["lat"])
        lon = float(st.session_state["lon"])
        hour = int(st.session_state["hour"])
    except ValueError:
        st.error("緯度・経度・時刻を正しく入力してください。")
        return

    target_dt = datetime.now().replace(minute=0, second=0, microsecond=0, hour=hour)
    try:
        cloud, visibility = fetch_weather(lat, lon, target_dt)
    except Exception as exc:  # noqa: BLE001
        st.error(f"天気APIの取得に失敗しました: {exc}")
        return

    moon_pct = round(moon_illumination(target_dt) * 100)
    st.session_state["cloud"] = round(cloud)
    st.session_state["vis"] = round(visibility)
    st.session_state["moon"] = moon_pct
    st.success(
        f"自動取得完了: 雲量 {cloud:.1f}%, 視程 {visibility:.1f} km, 月明かり推定 {moon_pct}%"
    )
    st.experimental_rerun()


def show_best_conditions() -> None:
    st.info(
        "理想に近い観測条件の目安:\n"
        "- 場所: 緯度10〜45度帯。都市光害が少ない開けた場所。雷雲から水平距離50〜150km離れて側方〜背後を狙う。\n"
        "- 季節/時間: 暖候期(5〜9月)。時刻は21〜02時が最有利、18〜20時/3〜5時が次点。\n"
        "- 気象: 雷活動が非常に活発(落雷多いセル/強エコー)。雲量20%以下。視程20km以上。降水域の真下は避ける。\n"
        "- 光条件: 新月〜三日月や月が低い/陰るタイミング。街灯や車灯が少ない暗所で暗順応。\n"
        "- 観測姿勢: 雷雲の真上ではなく少し離れた上空を注視。広角・長秒露光+三脚、連写/インターバル撮影で記録。"
    )


def show_formula() -> None:
    with st.expander("計算方式を見る", expanded=False):
        st.markdown(
            "\n".join(
                [
                    "- 台形スコアで0〜1に正規化後、重み付き和で z を計算しロジスティック変換",
                    "  緯度: low=-10, 最適=10〜45, high=60 → 重み0.6",
                    "  季節: low=2.5, 最適=5〜9, high=11.5 → 重み0.5",
                    "  時刻: 21-02時=1.0, 18-20/3-5時=0.6, それ以外=0.1 → 重み0.4",
                    "  雷活動: (0〜10)/10 → 重み2.0",
                    "  雲量: (1 - 雲量%/100) → 重み0.4",
                    "  月明かり: (1 - 明るさ%/100) → 重み0.2",
                    "  視程: (km/40) → 重み0.6",
                    "- z = -3.0 + Σ(重み×スコア), 確率 = 1/(1+exp(-z)) を0〜100%表示",
                    "- 70%以上: 良好, 40%以上: 並, それ未満: 弱め のヒント",
                ]
            )
        )


def main() -> None:
    init_state()
    st.title("レッドスプライト観測予測 (Web版)")

    # アクションを先に処理することで、状態更新後に入力ウィジェットが再描画される
    render_actions()

    col_map, col_form = st.columns([1, 1])
    with col_map:
        render_map()
    with col_form:
        render_inputs()

    show_formula()


if __name__ == "__main__":
    main()
