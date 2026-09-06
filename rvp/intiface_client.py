"""Intiface Central との通信クライアント(最小版: linear のみ)。

buttplug-py (pip install buttplug-py) を使用。
Intiface Central 側でサーバーを起動しておくこと(既定: ws://127.0.0.1:12345)。
"""

import asyncio
import logging
from .i18n import tr

from buttplug import Client, WebsocketConnector, ProtocolSpec

logger = logging.getLogger("rvp.intiface")


def _install_buttplug_guards() -> None:
    """buttplug-py 3.x の脆弱点2つへの自衛パッチ(=122)。

    RVPはアイテム終了・ステート移行・イベント終了のたびにデバイス駆動タスクを
    cancel() する。キャンセルがコマンドの応答待ち(`Client.send` の
    `await future`)の最中に着弾すると、**キャンセル済みfutureが
    `Client._tasks` に残留**する(削除は応答受信時のみ)。直後に届くOk応答が
    `set_result(キャンセル済みfuture)` → InvalidStateError →
    `WebsocketConnector._handle_messages`(受信ループタスク)が例外で死亡し、
    **以後この接続は全コマンドが永久ハング**する(Windows実機 2026-08-13報告。
    短いアイテムを並べたスクリプト専用chで頻発=境界のキャンセル回数が多い)。

    対策(CTk 6.0.0 の =84/=95/=108 と同じ自衛monkeypatch方針):
      A. `Client.send` をラップし、例外(キャンセル含む)時に自分のfutureを
         `_tasks` から掃除して再送出する。遅れて届く応答は buttplug 側の
         「unexpected Id」ログ1行で済む(受信ループは死なない)。
      B. `Client._handle_message` をラップし、例外を警告ログに封じ込めて
         受信ループを生かし続ける(想定外の重複応答等に対する最終防壁)。
    どちらも挙動追加はなく「壊れ方を無害化」するだけ。バージョン差異で
    当たらない場合に備え、失敗しても起動は続行する(警告のみ)。
    """
    if getattr(Client, "_rvp_guarded", False):
        return
    try:
        orig_send = Client.send

        async def _send_guarded(self, message):
            try:
                return await orig_send(self, message)
            except BaseException:
                # キャンセル・切断時に未解決futureを掃除(遅延応答での
                # set_result事故=受信ループ死亡を防ぐ)
                try:
                    self._tasks.pop(message.id, None)
                except Exception:
                    pass
                raise

        orig_handle = Client._handle_message

        async def _handle_guarded(self, message):
            try:
                await orig_handle(self, message)
            except Exception as e:
                # 受信ループ(_handle_messages)を殺さない。CancelledError は
                # BaseException なのでここを素通りする(タスク終了は妨げない)。
                logger.warning(
                    tr("buttplug応答の処理でエラー(接続は継続します): %s"), e)

        Client.send = _send_guarded
        Client._handle_message = _handle_guarded
        Client._rvp_guarded = True
    except Exception as e:
        logger.warning(tr("buttplug自衛パッチの適用に失敗: %s"), e)


_install_buttplug_guards()


async def probe_ws_port(url: str, timeout: float = 1.0) -> bool:
    """ws://host:port のポートが開いているかだけを確かめる軽量プローブ(=78)。

    自動接続の事前チェック用。Intiface Central のサーバーが listen して
    いなければ localhost では即座に拒否されるため、失敗コストはほぼゼロ
    (buttplug クライアントの生成・ハンドシェイク・ログ出力は発生しない)。
    プロセス存在確認ではなくポートを見るのは、①アプリ起動中でもサーバーが
    停止していることがある(Start Server ボタン) ②依存追加やサブプロセス
    起動(tasklist)が不要 ③URL欄のホスト/ポート変更にそのまま追従する、の3点。
    """
    from urllib.parse import urlsplit
    try:
        p = urlsplit(url if "//" in url else "//" + url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 12345
    except ValueError:
        return False
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


class IntifaceClient:
    def __init__(self, url: str = "ws://127.0.0.1:12345"):
        self.url = url
        self._client: Client | None = None
        # =122: connect() の直列化。自動接続(=78)と手動接続が重なると、
        # 片方の disconnect がもう片方のスキャン中の接続を切り、
        # StopScanning 送信エラー+一時的な未接続状態になるため
        # (Windows実機 2026-08-13 のログ冒頭で観測)。
        # asyncio.Lock はPython 3.10+では最初の acquire でループに束縛される
        # ので、メインスレッドで生成して AsyncRunner のループで使ってよい。
        self._connect_lock = asyncio.Lock()
        self.connected = False
        # linear駆動区間: funscriptのpos 0-100 をこの区間へ線形マッピングする
        self.range_min = 0
        self.range_max = 100
        # デバイス座標の反転。デバイスによって上下が逆の場合にONにする。
        self.invert = False
        # =79 TWIST(2軸linearデバイスの2軸目)。linearと同じ意味論の独立レーン。
        self.twist_min = 0
        self.twist_max = 100
        self.twist_invert = False
        # rotate: 回転方向の反転。正回転/負回転が逆のデバイス用。
        self.rotate_invert = False
        # rotate(A10サイクロンSA)専用の反転
        self.rotate_a10_invert = False
        # 実行強度レンジ(0.0〜1.0の下限/上限)。動作中の出力を[min,max]へ線形写像する。
        # 停止(rotate: pos=50 / vibration: pos=0)は下限に関わらず停止のまま。既定0-100%。
        self.rotate_min = 0.0
        self.rotate_max = 1.0
        # rotate(A10サイクロンSA)専用の強度レンジ
        self.rotate_a10_min = 0.0
        self.rotate_a10_max = 1.0
        self.vibration_min = 0.0
        self.vibration_max = 1.0
        # 回転デバイスの割り当て(デバイス名 → レーン)。値は
        #   "ufo"/"a10"(全ロータ同一) または ["ufo","a10"] のロータ順リスト
        #   (=120: 2ロータ機のロータ単位分割)。
        # ユーザーが接続タブで上書きした分のみ保持し、未登録は名前ベースの
        # 既定(A10系=a10 / それ以外=ufo)を使う。再接続をまたいで名前で復元する。
        self.rotate_assign: dict = {}

    # レーン識別子(ROTATE(ufo) / ROTATE(a10cyclonesa))
    LANE_UFO = "ufo"
    LANE_A10 = "a10"

    @staticmethod
    def is_a10_name(name: str) -> bool:
        """デバイス名がA10サイクロンSA系かを判定する。

        名前を正規化(小文字化・空白/ハイフン/アンダースコア除去)して
        「a10cyclone」を含むかで判定する。「Vorze A10 Cyclone SA」等の
        表記揺れに強い。
        """
        norm = "".join(c for c in (name or "").lower()
                       if c not in " -_　")
        return "a10cyclone" in norm

    def set_rotate_range(self, rmin: int, rmax: int) -> None:
        """回転強度レンジを設定する(0-100のUI値)。再生中でも即時反映される。"""
        rmin = max(0, min(100, int(rmin)))
        rmax = max(rmin, min(100, int(rmax)))
        self.rotate_min = rmin / 100.0
        self.rotate_max = rmax / 100.0

    def set_rotate_a10_range(self, rmin: int, rmax: int) -> None:
        """rotate(A10)の強度レンジを設定する(0-100のUI値)。"""
        rmin = max(0, min(100, int(rmin)))
        rmax = max(rmin, min(100, int(rmax)))
        self.rotate_a10_min = rmin / 100.0
        self.rotate_a10_max = rmax / 100.0

    def set_vibration_range(self, rmin: int, rmax: int) -> None:
        """振動強度レンジを設定する(0-100のUI値)。再生中でも即時反映される。"""
        rmin = max(0, min(100, int(rmin)))
        rmax = max(rmin, min(100, int(rmax)))
        self.vibration_min = rmin / 100.0
        self.vibration_max = rmax / 100.0

    def map_rotate_speed(self, pos: int) -> tuple[float, bool]:
        """posを実出力の(speed, clockwise)へ変換する(強度レンジ適用済み)。

        pos=50は強度レンジに関わらず停止(speed 0)。動作中(pos≠50)は
        速度率|pos-50|/50を[rotate_min, rotate_max]へ線形写像する。
        例: レンジ20-100でpos=75 → 0.5 → 20+0.5*(100-20)=60%
        """
        frac, clockwise = self.rotate_params(pos)
        if frac <= 0.0:
            return 0.0, clockwise
        speed = self.rotate_min + frac * (self.rotate_max - self.rotate_min)
        return max(0.0, min(1.0, speed)), clockwise

    def map_rotate_a10_speed(self, pos: int) -> tuple[float, bool]:
        """rotate(A10)用のpos→(speed, clockwise)変換(専用レンジ適用)。"""
        frac, clockwise = self.rotate_params(pos)
        if frac <= 0.0:
            return 0.0, clockwise
        speed = self.rotate_a10_min + frac * (self.rotate_a10_max
                                              - self.rotate_a10_min)
        return max(0.0, min(1.0, speed)), clockwise

    def map_vibration_speed(self, pos: int) -> float:
        """posを実出力の振動強度へ変換する(強度レンジ適用済み)。

        pos=0は強度レンジに関わらず停止。pos=1〜100は
        速度率pos/100を[vibration_min, vibration_max]へ線形写像する。
        """
        pos = max(0, min(100, pos))
        if pos <= 0:
            return 0.0
        frac = pos / 100.0
        speed = self.vibration_min + frac * (self.vibration_max - self.vibration_min)
        return max(0.0, min(1.0, speed))

    def set_linear_range(self, range_min: int, range_max: int) -> None:
        """駆動区間を設定する(0 <= min <= max <= 100)。再生中でも即時反映される。"""
        range_min = max(0, min(100, int(range_min)))
        range_max = max(0, min(100, int(range_max)))
        if range_min > range_max:
            range_min, range_max = range_max, range_min
        self.range_min = range_min
        self.range_max = range_max

    def map_pos(self, pos: int) -> int:
        """funscriptのpos(0-100)を駆動区間内の実位置へ変換する。"""
        pos = max(0, min(100, pos))
        return round(self.range_min + pos / 100.0 * (self.range_max - self.range_min))

    def set_twist_range(self, range_min: int, range_max: int) -> None:
        """TWISTの駆動区間を設定する(=79。意味論は set_linear_range と同一)。"""
        range_min = max(0, min(100, int(range_min)))
        range_max = max(0, min(100, int(range_max)))
        if range_min > range_max:
            range_min, range_max = range_max, range_min
        self.twist_min = range_min
        self.twist_max = range_max

    def map_twist_pos(self, pos: int) -> int:
        """funscriptのpos(0-100)をTWIST駆動区間内の実位置へ変換する(=79)。"""
        pos = max(0, min(100, pos))
        return round(self.twist_min + pos / 100.0 * (self.twist_max - self.twist_min))

    async def connect(self, scan_seconds: float = 3.0) -> None:
        """接続してデバイスをスキャンする。

        =122: 全体を _connect_lock で直列化する。重なった接続要求
        (自動接続=78と手動接続など)は先行の完了(スキャン込み)を待ってから
        走る。disconnect() はロックを取らない(connect が内側で呼ぶため。
        アプリ終了時などに外から呼ばれてスキャン中の接続を切っても、
        stop_scanning の失敗は下の except で握り潰される=無害)。
        """
        async with self._connect_lock:
            await self.disconnect()
            client = Client("RVP", ProtocolSpec.v3)
            connector = WebsocketConnector(self.url, logger=client.logger)
            await client.connect(connector)
            self._client = client
            self.connected = True
            try:
                await client.start_scanning()
                await asyncio.sleep(scan_seconds)
                await client.stop_scanning()
            except Exception as e:
                logger.warning(tr("スキャン中にエラー: %s"), e)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass
        self._client = None
        self.connected = False

    def device_names(self) -> list[str]:
        """接続中の全デバイス名(linear対応可否付き)。"""
        if not self._client:
            return []
        names = []
        for dev in self._client.devices.values():
            tags = []
            if dev.linear_actuators:
                # =79: 2軸(linear+twist)デバイスはタグで分かるようにする
                tags.append("linear+twist" if len(dev.linear_actuators) >= 2
                            else "linear")
            if dev.rotatory_actuators:
                # 現在の割り当てレーンを表示する(ufo / a10cyclonesa)。
                # =120: 分割割り当てはロータ別に併記する。
                lanes = self.rotor_lanes(getattr(dev, "name", ""),
                                         len(dev.rotatory_actuators))
                seen = []
                for ln in lanes:
                    if ln not in seen:
                        seen.append(ln)
                tags.extend("rotate(a10cyclonesa)" if ln == self.LANE_A10
                            else "rotate(ufo)" for ln in seen)
            if any(getattr(a, "type", "Vibrate") == "Vibrate" for a in dev.actuators):
                tags.append("vibration")
            tag = f" [{', '.join(tags)}]" if tags else ""
            names.append(f"{dev.name}{tag}")
        return names

    def _linear_actuators(self):
        """linear(ストローク)用アクチュエータ=各デバイスの1軸目のみ(=79)。

        =78以前は全デバイスの全linearアクチュエータへ一斉送信していたが、
        2軸デバイス(linear+twist)では2軸目にストローク信号が届いて
        意図せずねじれるため、1軸目(index最小)だけへ送るよう変更した
        (1軸デバイスでは挙動不変)。
        """
        if not self._client:
            return []
        acts = []
        for dev in self._client.devices.values():
            la = dev.linear_actuators
            if la:
                acts.append(la[0])
        return acts

    def _twist_actuators(self):
        """TWIST用アクチュエータ=各デバイスのlinearアクチュエータの2軸目(=79)。"""
        if not self._client:
            return []
        acts = []
        for dev in self._client.devices.values():
            la = dev.linear_actuators
            if la and len(la) >= 2:
                acts.append(la[1])
        return acts

    def default_lane_of(self, name: str) -> str:
        """デバイス名からの既定レーン(A10系=a10 / それ以外=ufo)。"""
        return self.LANE_A10 if self.is_a10_name(name) else self.LANE_UFO

    def lane_of(self, name: str) -> str:
        """デバイスの現在の割り当てレーン(ユーザー上書き優先、無ければ既定)。

        =120: ロータ単位割り当て(リスト値)のデバイスは先頭ロータのレーンを
        返す(デバイス単位のレーンを前提とする旧API互換)。ロータ別が必要な
        箇所は rotor_lanes() を使うこと。
        """
        v = self.rotate_assign.get(name)
        if isinstance(v, (list, tuple)) and v:
            return v[0]
        if v in (self.LANE_UFO, self.LANE_A10):
            return v
        return self.default_lane_of(name)

    def rotor_lanes(self, name: str, rotors: int) -> list:
        """デバイスの各ロータの割り当てレーンをロータ順のリストで返す(=120)。

        文字列割り当て(従来)・未設定はどのロータも同一レーン。リスト割り当ては
        ロータ数に合わせて末尾を複製/切り詰めして返す(想定外のロータ数でも
        安全側に倒す)。
        """
        if rotors <= 0:
            return []
        v = self.rotate_assign.get(name)
        if isinstance(v, (list, tuple)) and v:
            lanes = [x if x in (self.LANE_UFO, self.LANE_A10)
                     else self.default_lane_of(name) for x in v]
            while len(lanes) < rotors:
                lanes.append(lanes[-1])
            return lanes[:rotors]
        base = v if v in (self.LANE_UFO, self.LANE_A10) \
            else self.default_lane_of(name)
        return [base] * rotors

    def set_rotate_assign(self, name: str, lane) -> None:
        """回転デバイスの割り当てレーンを設定する。

        lane: "ufo"/"a10"(全ロータ同一)またはロータ順リスト(=120)。
        リストの全要素が同一なら文字列へ正規化して保持する(コンフィグを
        従来形式に保つ+分割判定を単純にする)。不正値は無視。
        """
        if not name:
            return
        if isinstance(lane, (list, tuple)):
            lanes = [x for x in lane if x in (self.LANE_UFO, self.LANE_A10)]
            if not lanes or len(lanes) != len(lane):
                return
            if all(x == lanes[0] for x in lanes):
                self.rotate_assign[name] = lanes[0]
            else:
                self.rotate_assign[name] = list(lanes)
        elif lane in (self.LANE_UFO, self.LANE_A10):
            self.rotate_assign[name] = lane

    def set_rotate_rotor_assign(self, name: str, index: int, lane: str,
                                rotors: int) -> None:
        """1ロータ分だけ割り当てレーンを変更する(=120・割り当てUI用)。

        現在のロータ別レーンを起点に index 番目だけ差し替える。
        """
        if (lane not in (self.LANE_UFO, self.LANE_A10) or not name
                or not (0 <= index < rotors)):
            return
        lanes = self.rotor_lanes(name, rotors)
        lanes[index] = lane
        self.set_rotate_assign(name, lanes)

    def rotate_devices(self) -> list:
        """接続中の回転デバイス一覧を返す(割り当てUI用)。

        戻り値: [{"name", "lane", "lanes", "rotors", "is_a10"}] を接続順で。
        lane=先頭ロータのレーン(旧API互換) / lanes=ロータ順のレーン(=120)。
        """
        out = []
        if not self._client:
            return out
        for dev in self._client.devices.values():
            rot = dev.rotatory_actuators
            if rot:
                name = getattr(dev, "name", "")
                lanes = self.rotor_lanes(name, len(rot))
                out.append({
                    "name": name,
                    "lane": lanes[0] if lanes else self.lane_of(name),
                    "lanes": lanes,
                    "rotors": len(rot),
                    "is_a10": self.is_a10_name(name),
                })
        return out

    def _rotatory_actuators(self, a10: bool = False):
        """回転アクチュエータを割り当てレーン別に返す。

        既定(ユーザー未設定)ではデバイス名ベース(A10系=a10 / 他=ufo)なので
        従来挙動と一致する。ユーザーが割り当てを上書きするとそれに従う。
        =120: ロータ単位で判定する(分割割り当てのデバイスは該当ロータのみ)。
        """
        if not self._client:
            return []
        lane = self.LANE_A10 if a10 else self.LANE_UFO
        acts = []
        for dev in self._client.devices.values():
            rot = dev.rotatory_actuators
            if not rot:
                continue
            lanes = self.rotor_lanes(getattr(dev, "name", ""), len(rot))
            acts.extend(a for a, ln in zip(rot, lanes) if ln == lane)
        return acts

    @property
    def has_linear(self) -> bool:
        return bool(self._linear_actuators())

    @property
    def has_twist(self) -> bool:
        return bool(self._twist_actuators())

    @property
    def has_rotate(self) -> bool:
        return bool(self._rotatory_actuators())

    @property
    def has_rotate_a10(self) -> bool:
        return bool(self._rotatory_actuators(a10=True))

    def _vibration_actuators(self):
        """振動系(スカラー)アクチュエータを返す。

        v3のScalarActuatorはtype属性を持つのでVibrateのみ、
        v1のVibrateActuatorはtype属性がないため無条件で対象とする。
        """
        if not self._client:
            return []
        acts = []
        for dev in self._client.devices.values():
            for a in dev.actuators:
                if getattr(a, "type", "Vibrate") == "Vibrate":
                    acts.append(a)
        return acts

    @property
    def has_vibrate(self) -> bool:
        return bool(self._vibration_actuators())

    @staticmethod
    def rotate_params(pos: int) -> tuple[float, bool]:
        """funscriptのpos(0-100)を (speed 0.0-1.0, clockwise) に変換する。

        仕様:
          pos=50  → 停止 (speed 0)
          pos=100 → 正回転(clockwise=True)の最大速度
          pos=0   → 負回転(clockwise=False)の最大速度
        50を中心に、離れるほど速く回る。
        """
        pos = max(0, min(100, pos))
        clockwise = pos >= 50
        speed = abs(pos - 50) / 50.0   # 0.0〜1.0
        return speed, clockwise

    async def send_linear(self, duration_ms: int, pos: int) -> None:
        """全 linear アクチュエータへ移動コマンドを送る。

        pos: 0-100 (funscript の値)。処理順:
          1. 駆動区間へマッピング (funscript座標系のまま)
          2. invert が有効なら区間内で折り返す (min+max - x)
          3. buttplug の 0.0-1.0 に変換して送出
        表示用の位置は map_pos() の値(funscript座標系)を使うこと。

        =86: 反転は「区間の入れ替え」(x-y → y-x)。旧仕様の 0-100 座標反転
        (100-x)だと区間0-50の反転が100-50(先端往復)になってしまうが、
        本仕様では50-0=根本往復のまま奥/手前の向きだけが入れ替わる
        (自然な"反転"。ユーザー依頼2)。区間が0-100のときは従来と同じ結果。
        """
        mapped = self.map_pos(pos)
        device_pos = (self.range_min + self.range_max - mapped) \
            if self.invert else mapped
        duration_ms = max(20, int(duration_ms))
        # buttplugのLinearCmdは position=1.0 ちょうどだと無反応になるデバイスが
        # あるため、送信値を安全域にクランプする(0.0側も同様に僅かに内側へ)。
        target = device_pos / 100.0
        target = max(0.005, min(0.995, target))
        for act in self._linear_actuators():
            try:
                await act.command(duration_ms, target)
            except Exception as e:
                logger.error(tr("linearコマンド送信失敗: %s"), e)
                self.connected = False
                raise

    async def send_twist(self, duration_ms: int, pos: int) -> None:
        """全 twist アクチュエータ(2軸目)へ移動コマンドを送る(=79)。

        pos: 0-100 (funscript の値)。処理は send_linear と同一で、
        レンジ(twist_min/max)・反転(twist_invert)だけが独立レーン。
        反転は=86の「区間の入れ替え」方式(send_linear参照)。
        """
        mapped = self.map_twist_pos(pos)
        device_pos = (self.twist_min + self.twist_max - mapped) \
            if self.twist_invert else mapped
        duration_ms = max(20, int(duration_ms))
        target = device_pos / 100.0
        target = max(0.005, min(0.995, target))
        for act in self._twist_actuators():
            try:
                await act.command(duration_ms, target)
            except Exception as e:
                logger.error(tr("twistコマンド送信失敗: %s"), e)
                self.connected = False
                raise

    async def send_rotate(self, pos: int) -> None:
        """通常のrotateアクチュエータ(A10系を除く)へ回転コマンドを送る。

        pos: 0-100 (funscript の値)。pos=50で停止、100で正回転、0で負回転。
        A10サイクロンSA系は send_rotate_a10 が担当する(完全分離)。
        """
        speed, clockwise = self.map_rotate_speed(pos)
        if self.rotate_invert:
            clockwise = not clockwise
        for act in self._rotatory_actuators():
            try:
                await act.command(speed, clockwise)
            except Exception as e:
                logger.error(tr("rotateコマンド送信失敗: %s"), e)
                self.connected = False
                raise

    async def send_rotate_a10(self, pos: int) -> None:
        """A10サイクロンSA系のrotateアクチュエータへ回転コマンドを送る。

        pos仕様はrotateと同一。専用のレンジ・反転設定を適用する。
        """
        speed, clockwise = self.map_rotate_a10_speed(pos)
        if self.rotate_a10_invert:
            clockwise = not clockwise
        for act in self._rotatory_actuators(a10=True):
            try:
                await act.command(speed, clockwise)
            except Exception as e:
                logger.error(tr("rotate(a10)コマンド送信失敗: %s"), e)
                self.connected = False
                raise

    # ---- レーン単位の多チャンネル送信(CSV/2ロータ対応) ----

    def _lane_range(self, lane: str) -> tuple[float, float]:
        if lane == self.LANE_A10:
            return self.rotate_a10_min, self.rotate_a10_max
        return self.rotate_min, self.rotate_max

    def _lane_invert(self, lane: str) -> bool:
        return self.rotate_a10_invert if lane == self.LANE_A10 \
            else self.rotate_invert

    @staticmethod
    def map_channels_to_rotors(channel_vals: list, rotors: int,
                               swap: bool = False) -> list:
        """ソースのチャンネル値(N個)をデバイスのロータ(M個)へ写像する。

        channel_vals: [(clockwise, frac), ...] 長さ N(=1 or 2)。
        戻り値: 各ロータへの [(clockwise, frac), ...] 長さ rotors。
          N=1,M=1: そのまま / N=1,M=2: 両ロータへ複製
          N=2,M=1: 速度の大きい方を採用(両0=停止)。同速はch0(左)優先=OR
          N=2,M=2: ch0→ロータ0, ch1→ロータ1(swap=True で左右入替)
        """
        if rotors <= 0 or not channel_vals:
            return []
        n = len(channel_vals)
        if n == 1:
            return [channel_vals[0]] * rotors
        # n == 2
        a, b = channel_vals[0], channel_vals[1]
        if rotors == 1:
            # OR: 速度率(frac)の大きい方。同値は a(ch0=左)優先。
            return [a if a[1] >= b[1] else b]
        left, right = (b, a) if swap else (a, b)
        out = [left, right]
        # 3ロータ以上の想定外機はch0で埋める(安全側)
        while len(out) < rotors:
            out.append(a)
        return out[:rotors]

    async def send_rotate_lane(self, lane: str, channel_vals: list,
                               swap: bool = False) -> None:
        """レーンに割り当たった全デバイスへ、ロータ個別に回転指令を送る。

        channel_vals: ソースの各チャンネル (clockwise, frac0.0-1.0)。
        frac=0 は停止(レンジ下限に関わらず speed 0)。レンジ/反転はレーン設定。
        各デバイスのロータ数に応じて map_channels_to_rotors で写像する。
        """
        if not self._client:
            return
        rmin, rmax = self._lane_range(lane)
        inv = self._lane_invert(lane)
        for dev in self._client.devices.values():
            rot = dev.rotatory_actuators
            if not rot:
                continue
            # =120: このレーンに割り当たったロータだけ(ロータ順)。分割
            # 割り当てのufotwは片側1ロータになり、2chソースは既存の
            # N=2→M=1(OR)規則で写像される。
            lanes = self.rotor_lanes(getattr(dev, "name", ""), len(rot))
            acts = [a for a, ln in zip(rot, lanes) if ln == lane]
            if not acts:
                continue
            cmds = self.map_channels_to_rotors(channel_vals, len(acts), swap)
            for act, (cw, frac) in zip(acts, cmds):
                if frac <= 0.0:
                    speed = 0.0
                else:
                    speed = rmin + frac * (rmax - rmin)
                    speed = max(0.0, min(1.0, speed))
                clockwise = (not cw) if inv else cw
                try:
                    await act.command(speed, clockwise)
                except Exception as e:
                    logger.error(tr("rotateコマンド送信失敗: %s"), e)
                    self.connected = False
                    raise

    async def send_vibration(self, pos: int) -> None:
        """全 vibration アクチュエータへ振動コマンドを送る。

        pos: 0-100 (funscript の値)。pos=0で停止、pos=100で最大振動。
        振動強度レンジ[vibration_min, vibration_max]へ線形写像した強度で送出する。
        """
        speed = self.map_vibration_speed(pos)
        for act in self._vibration_actuators():
            try:
                await act.command(speed)
            except Exception as e:
                logger.error(tr("vibrationコマンド送信失敗: %s"), e)
                self.connected = False
                raise

    async def stop_all(self) -> None:
        """全デバイスの動作を停止する。"""
        if not self._client:
            return
        for dev in self._client.devices.values():
            try:
                await dev.stop()
            except Exception as e:
                logger.warning(tr("停止コマンド送信失敗: %s"), e)
