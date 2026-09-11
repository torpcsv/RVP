"""メイン画面: 設定の復元(_apply_saved_config)と保存(save_app_config)(RVPApp の mixin)。"""
from __future__ import annotations

import customtkinter as ctk
import os
from ..scenario import TRACK_ROTATE_A10
from .. import appfont, apptheme
from ..i18n import load_config, save_config, tr



class _RVPAppConfigMixin:
    """RVPApp の mixin(=301 分割)。設定の復元(_apply_saved_config)と保存(save_app_config)"""

    def _apply_saved_config(self):
        """保存済みコンフィグをUIとプレーヤーへ復元する(不正値は無視)。"""
        cfg = load_config()
        mp = cfg.get("mpv_path")
        if isinstance(mp, str) and mp:
            self.mpv_path_var.set(mp)
            self.player.mpv_path = mp
        # =107: mpv欄の開閉(既定=閉じる)。
        self._set_mpv_open(bool(cfg.get("mpv_open", False)), save=False)

        def num(v):
            return isinstance(v, (int, float)) and not isinstance(v, bool)

        def rng(key):
            v = cfg.get(key)
            if (isinstance(v, list) and len(v) == 2
                    and num(v[0]) and num(v[1])):
                lo = max(0, min(100, int(v[0])))
                hi = max(0, min(100, int(v[1])))
                if lo <= hi:
                    return lo, hi
            return None

        vol = cfg.get("volume")
        if num(vol):
            vol = max(0, min(100, int(vol)))
            self.volume_slider.set(vol)
            self._on_volume_change(vol)
        r = rng("linear_range")
        if r:
            self.range_slider.set_values(*r)
        r = rng("rotate_range")
        if r:
            self.rotate_scale_slider.set_values(*r)
        r = rng("rotate_a10_range")
        if r:
            self.a10_scale_slider.set_values(*r)
        r = rng("vibration_range")
        if r:
            self.vibration_scale_slider.set_values(*r)
        if isinstance(cfg.get("linear_invert"), bool):
            self.invert_var.set(cfg["linear_invert"])
            self._on_invert_change()
        if isinstance(cfg.get("rotate_invert"), bool):
            self.rotate_invert_var.set(cfg["rotate_invert"])
            self._on_rotate_invert_change()
        if isinstance(cfg.get("rotate_a10_invert"), bool):
            self.a10_invert_var.set(cfg["rotate_a10_invert"])
            self._on_a10_invert_change()
        offsets = cfg.get("offsets")
        if isinstance(offsets, dict):
            for dtype in ("linear", "twist", "rotate",
                          TRACK_ROTATE_A10, "vibration"):
                v = offsets.get(dtype)
                if num(v):
                    self.offset_vars[dtype].set(f"{float(v):.1f}")
                    self._commit_offset(dtype)
        sl = cfg.get("speed_limit")
        if sl in self.SPEED_LIMIT_MS:
            key2label = {v: k for k, v in self._speed_limit_labels().items()}
            self.speed_limit_var.set(key2label[sl])
            self._on_speed_limit_change()
        # =102/=103: グラフ表示モード(1枚表示・マイナス表示)を復元
        ov, mi = cfg.get("graph_overlay"), cfg.get("graph_minus")
        if isinstance(ov, bool) or isinstance(mi, bool):
            self._set_graph_mode(bool(ov) if isinstance(ov, bool) else False,
                                 bool(mi) if isinstance(mi, bool) else False)
        # =104: グラフ更新頻度(30/60fps)を復元
        gfps = cfg.get("graph_fps")
        if gfps in (30, 60):
            self.graph_fps_var.set(f"{gfps}fps")
            self._graph_interval_ms = 33 if gfps == 30 else 16
        # =262: 背景イラストの表示ON/OFFを復元
        if isinstance(cfg.get("show_background"), bool):
            self.show_bg_var.set(cfg["show_background"])
            self.bg_art.set_user_enabled(cfg["show_background"])
        # =263: 透け具合(weak/mid/strong)を復元
        level = cfg.get("bg_alpha")
        for ja, lv in self._BG_ALPHA_LABELS:
            if level == lv:
                self.bg_alpha_var.set(tr(ja))
                self.bg_art.set_alpha_level(lv)
                break
        # 回転デバイスの割り当てを復元(名前→レーン。有効なレーン値のみ採用)
        assign = cfg.get("rotate_assign")
        if isinstance(assign, dict):
            for name, lane in assign.items():
                if isinstance(name, str):
                    # =120: 値は "ufo"/"a10" またはロータ順リスト。
                    # 検証は set_rotate_assign 側(不正値は無視)。
                    self.intiface.set_rotate_assign(name, lane)
        # ROTATEレーンの左右反転を復元(player と チェックへ反映)
        swap = cfg.get("rotate_swap")
        if isinstance(swap, dict):
            for lane, var in (("ufo", self.rotate_swap_var),
                              ("a10", self.a10_swap_var)):
                if isinstance(swap.get(lane), bool):
                    var.set(swap[lane])
                    self.player.rotate_swap[lane] = swap[lane]
        # =78: Intiface Central への自動接続(既定ON。設定があれば復元)
        if isinstance(cfg.get("auto_connect"), bool):
            self.auto_connect_var.set(cfg["auto_connect"])
        # =80: TCode直結のポート/ボーレート
        if isinstance(cfg.get("tcode_port"), str) and cfg["tcode_port"]:
            vals = list(self.tcode_port_menu.cget("values"))
            if cfg["tcode_port"] not in vals:
                vals = [v for v in vals if v] + [cfg["tcode_port"]]
                self.tcode_port_menu.configure(values=vals)
            self.tcode_port_var.set(cfg["tcode_port"])
        if isinstance(cfg.get("tcode_baud"), str) and cfg["tcode_baud"]:
            self.tcode_baud_var.set(cfg["tcode_baud"])
        # =79: TWIST補正値(=88: twist_enabled は廃止・旧コンフィグの値は読み捨て)
        tr_ = rng("twist_range")
        if tr_:
            self.twist_range_slider.set_values(*tr_)
            self._on_twist_range_change(*tr_)
        if isinstance(cfg.get("twist_invert"), bool):
            self.twist_invert_var.set(cfg["twist_invert"])
            self._on_twist_invert_change()
        last_dir = cfg.get("last_dir")
        if isinstance(last_dir, str) and os.path.isdir(last_dir):
            self._last_dir = last_dir
        # =119: UIフォントの選択を復元(設定メニューの表示用。適用は起動時)
        fam = appfont.configured_family(cfg)   # =293 キー欠落は初期値
        self.font_var.set(fam if fam else tr("システム標準"))
        # 外観テーマを復元(dark/light=即時反映。=134: パステルはライト基調
        # +色の適用は起動時にmain()が実施済みなので、ここでは状態と表示のみ)
        appr = cfg.get("appearance")
        if appr in apptheme.THEMES:
            self.color_theme = appr
            self.appearance_mode = "light"
            ctk.set_appearance_mode("light")
            self._update_theme_btn()
            self._refresh_slider_theme()
            self._refresh_arrow_icons()
        elif appr in ("dark", "light"):
            self.appearance_mode = appr
            ctk.set_appearance_mode(appr)
            self._update_theme_btn()
            self._refresh_slider_theme()
            self._refresh_arrow_icons()   # =111: tk.PhotoImageは自前で追従
        # 履歴リストを復元して表示
        self._refresh_history_list()

    def save_app_config(self):
        """現在の設定を ~/.rvp_config.json へ保存する(言語等の既存キーは保持)。"""
        cfg = load_config()
        # =88: 廃止したTWISTサブ機能スイッチの旧キーは保存時に取り除く
        cfg.pop("twist_enabled", None)
        cfg.update({
            "volume": int(self.volume_slider.get()),
            "linear_range": [self.range_slider.val_min,
                             self.range_slider.val_max],
            "linear_invert": bool(self.invert_var.get()),
            # =80 TCode直結のポート/ボーレート(接続状態は保存しない=手動接続)
            "tcode_port": self.tcode_port_var.get().strip(),
            "tcode_baud": self.tcode_baud_var.get().strip(),
            # =79 TWIST(2軸目)のサブ機能スイッチと補正値
            "twist_range": [self.twist_range_slider.val_min,
                            self.twist_range_slider.val_max],
            "twist_invert": bool(self.twist_invert_var.get()),
            "rotate_range": [self.rotate_scale_slider.val_min,
                             self.rotate_scale_slider.val_max],
            "rotate_invert": bool(self.rotate_invert_var.get()),
            "rotate_a10_range": [self.a10_scale_slider.val_min,
                                 self.a10_scale_slider.val_max],
            "rotate_a10_invert": bool(self.a10_invert_var.get()),
            "vibration_range": [self.vibration_scale_slider.val_min,
                                self.vibration_scale_slider.val_max],
            "offsets": {d: self.player.offsets_ms[d] / 1000.0
                        for d in ("linear", "twist", "rotate",
                                  TRACK_ROTATE_A10, "vibration")},
            "speed_limit": self._speed_limit_labels().get(
                self.speed_limit_var.get(), "mid"),
            # =134: パステル選択中はテーマ名を保存(dark/lightと同じキー)
            "appearance": self.color_theme or self.appearance_mode,
            # 回転デバイスの割り当て(名前→レーン)。ユーザー上書き分のみ保存。
            "rotate_assign": dict(self.intiface.rotate_assign),
            # ROTATEレーンの左右反転(ufotwタイプBのch入替)。
            "rotate_swap": {"ufo": bool(self.rotate_swap_var.get()),
                            "a10": bool(self.a10_swap_var.get())},
            # 動画プレーヤー(mpv)のパス(空=自動探索)。
            "mpv_path": self.mpv_path_var.get().strip(),
            # =107: 接続タブのmpv欄の開閉(既定=閉じる)。
            "mpv_open": bool(self._mpv_open),
            # =78: Intiface Central への自動接続。
            "auto_connect": bool(self.auto_connect_var.get()),
            # =102: グラフの1枚表示(集約)モード。=103: マイナス表示。
            "graph_overlay": bool(self.graph_view.overlay),
            "graph_minus": bool(self.graph_view.minus),
            # =104: グラフ更新頻度(30/60fps)。
            "graph_fps": 30 if self._graph_interval_ms >= 33 else 60,
            # =119: UIフォント(""=システム標準)。適用は起動時=再起動で反映。
            "font_family": ("" if self.font_var.get() == tr("システム標準")
                            else self.font_var.get()),
            # =262: 背景イラストの表示ON/OFF(視聴側設定)。
            "show_background": bool(self.show_bg_var.get()),
            # =263: 背景イラストの透け具合(weak/mid/strong)。
            "bg_alpha": self._bg_alpha_level(),
        })
        if self._last_dir:
            cfg["last_dir"] = self._last_dir
        save_config(cfg)
