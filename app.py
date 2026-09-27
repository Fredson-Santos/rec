"""
Diario - Gravador de Áudio (WASAPI Loopback)
Desenvolvido com CustomTkinter, SoundCard e SoundFile.
Grava exclusivamente o que você ouve no computador sem capturar microfone externo.
"""

import sys
import ctypes

# No Windows, inicializa o COM como STA antes de carregar bibliotecas de áudio (soundcard)
# Isso impede conflitos graves de thread (RPC_E_CHANGED_MODE / Access Violation) ao abrir filedialog
if sys.platform == "win32":
    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass

import os
import warnings

# Suprime avisos secundários de descontinuidade de áudio do WASAPI no terminal
warnings.filterwarnings("ignore", message=".*data discontinuity.*")

import time
import datetime
import threading
import numpy as np
import soundcard as sc
import soundfile as sf
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

# Configurações do CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class AudioLoopbackEngine:
    """Motor de gravação em thread separada usando WASAPI Loopback."""

    def __init__(self):
        self.is_recording = False
        self.is_paused = False
        self.thread = None
        self.current_filepath = None
        self.sound_file = None
        self.samplerate = 48000
        self.selected_speaker_name = None
        self.total_frames = 0
        self.current_rms = 0.0
        self.start_time = None
        self.elapsed_seconds = 0.0
        self._lock = threading.Lock()

        # Callbacks
        self.on_volume_update = None
        self.on_time_update = None
        self.on_finished = None
        self.on_error = None

    def start(self, speaker_name, filepath, samplerate=48000):
        if self.is_recording:
            return

        self.selected_speaker_name = speaker_name
        self.current_filepath = filepath
        self.samplerate = samplerate
        self.is_recording = True
        self.is_paused = False
        self.total_frames = 0
        self.current_rms = 0.0
        self.elapsed_seconds = 0.0
        self.start_time = time.time()

        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def pause(self):
        with self._lock:
            self.is_paused = True

    def resume(self):
        with self._lock:
            self.is_paused = False

    def stop(self):
        self.is_recording = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _worker(self):
        chunk_frames = int(self.samplerate * 0.05)  # Blocos de 50ms para responsividade
        com_initialized = False

        # No Windows, cada thread que acessa o WASAPI/áudio DEVE inicializar o COM
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED
                com_initialized = True
            except Exception:
                try:
                    ctypes.windll.ole32.CoInitialize(None)
                    com_initialized = True
                except Exception:
                    pass

        try:
            # Obtém dispositivo de loopback correspondente à saída escolhida
            mic = sc.get_microphone(id=str(self.selected_speaker_name), include_loopback=True)
            if not mic:
                raise RuntimeError(f"Não foi possível acessar o loopback para '{self.selected_speaker_name}'")

            # Cria o arquivo WAV para escrita incremental
            self.sound_file = sf.SoundFile(
                self.current_filepath,
                mode="w",
                samplerate=self.samplerate,
                channels=2,
                subtype="PCM_16"
            )

            last_timer_update = time.time()

            with mic.recorder(samplerate=self.samplerate, channels=2) as recorder:
                while self.is_recording:
                    # Captura bloco de áudio
                    data = recorder.record(numframes=chunk_frames)

                    # Se estiver pausado, apenas descarta o bloco para manter o buffer limpo
                    with self._lock:
                        paused = self.is_paused

                    if not paused:
                        self.sound_file.write(data)
                        self.total_frames += chunk_frames
                        self.elapsed_seconds += (chunk_frames / self.samplerate)

                        # Calcula volume RMS (0.0 a 1.0)
                        rms = float(np.sqrt(np.mean(data ** 2)))
                        self.current_rms = rms
                    else:
                        self.current_rms = 0.0

                    now = time.time()
                    if now - last_timer_update >= 0.05:
                        last_timer_update = now
                        if self.on_volume_update:
                            self.on_volume_update(self.current_rms)
                        if self.on_time_update:
                            self.on_time_update(self.elapsed_seconds, self.total_frames)

        except Exception as e:
            if self.on_error:
                self.on_error(str(e))
        finally:
            if self.sound_file and not self.sound_file.closed:
                try:
                    self.sound_file.flush()
                    self.sound_file.close()
                except Exception:
                    pass

            if com_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

            if self.on_finished:
                self.on_finished(self.current_filepath)


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Diario")
        self.geometry("780x780")
        self.minsize(700, 680)

        # Pasta padrão para gravações
        self.output_dir = os.path.join(os.path.expanduser("~"), "Music", "GravacoesPC")
        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir)
            except Exception:
                self.output_dir = os.path.abspath("gravacoes")
                os.makedirs(self.output_dir, exist_ok=True)

        self.engine = AudioLoopbackEngine()
        self.engine.on_volume_update = self._safe_volume_update
        self.engine.on_time_update = self._safe_time_update
        self.engine.on_finished = self._safe_finished
        self.engine.on_error = self._safe_error

        self.current_recording_file = None
        self.final_recording_file = None
        self.current_is_mp3 = True
        self.current_bitrate = 192
        self._is_compressing = False
        self.recent_recordings = []

        self._build_ui()
        self._load_audio_devices()
        self._bind_shortcuts()
        self._register_global_hotkeys()

        # Ativa o modo anti-captura de tela no Windows após a janela ser mapeada
        self.after(300, self._update_display_affinity)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Inicia oculto se o switch estiver ativo ou se --hidden foi passado na linha de comando
        if self.var_start_hidden.get() or "--hidden" in sys.argv:
            self.after(400, self._start_hidden)

    def _build_ui(self):
        # Container principal com scroll caso a tela seja redimensionada pequena
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # ----------------- HEADER -----------------
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, padx=24, pady=(20, 10), sticky="ew")
        header_frame.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        title_label = ctk.CTkLabel(
            title_box,
            text="🎙️ Diario",
            font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold")
        )
        title_label.pack(anchor="w")

        subtitle_label = ctk.CTkLabel(
            title_box,
            text="Captura exclusivamente o som interno do sistema (jogos, reuniões, vídeos) sem microfone.",
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color="#94a3b8"
        )
        subtitle_label.pack(anchor="w", pady=(2, 0))

        # Botão para ocultar/mostrar a janela (modo invisível total)
        self.btn_hide_window = ctk.CTkButton(
            header_frame,
            text="👁️ Ocultar Janela (Alt+H)",
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#334155",
            hover_color="#475569",
            width=180,
            height=34,
            command=self._toggle_window_visibility
        )
        self.btn_hide_window.grid(row=0, column=1, sticky="e", padx=(10, 0))

        # ----------------- CARD DE CONFIGURAÇÕES -----------------
        config_card = ctk.CTkFrame(self, corner_radius=12)
        config_card.grid(row=1, column=0, padx=24, pady=10, sticky="ew")
        config_card.grid_columnconfigure(1, weight=1)

        # Dispositivo de Saída
        lbl_dev = ctk.CTkLabel(config_card, text="Dispositivo de Saída:", font=ctk.CTkFont(weight="bold"))
        lbl_dev.grid(row=0, column=0, padx=16, pady=8, sticky="w")

        self.combo_devices = ctk.CTkOptionMenu(config_card, values=["Buscando dispositivos..."])
        self.combo_devices.grid(row=0, column=1, padx=(0, 8), pady=8, sticky="ew")

        self.btn_refresh_dev = ctk.CTkButton(
            config_card, text="🔄", width=36, command=self._load_audio_devices, fg_color="#334155", hover_color="#475569"
        )
        self.btn_refresh_dev.grid(row=0, column=2, padx=(0, 16), pady=8)

        # Formato de Saída e Taxa de Compressão (MP3 / WAV)
        lbl_fmt = ctk.CTkLabel(config_card, text="Formato & Compressão:", font=ctk.CTkFont(weight="bold"))
        lbl_fmt.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="w")

        fmt_box = ctk.CTkFrame(config_card, fg_color="transparent")
        fmt_box.grid(row=1, column=1, columnspan=2, padx=(0, 16), pady=(0, 8), sticky="ew")
        fmt_box.grid_columnconfigure(0, weight=3)
        fmt_box.grid_columnconfigure(1, weight=2)

        self.combo_format = ctk.CTkOptionMenu(
            fmt_box,
            values=["MP3 - Compacto (~90% menor)", "WAV - Original Sem Perdas"],
            command=self._on_format_changed
        )
        self.combo_format.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        self.combo_bitrate = ctk.CTkOptionMenu(
            fmt_box,
            values=["192 kbps (Recomendado)", "128 kbps (Ultra leve)", "320 kbps (Máx fidelidade)"],
            command=self._on_bitrate_changed
        )
        self.combo_bitrate.grid(row=0, column=1, sticky="ew")

        # Taxa de Amostragem (Frequência)
        lbl_rate = ctk.CTkLabel(config_card, text="Frequência de Áudio:", font=ctk.CTkFont(weight="bold"))
        lbl_rate.grid(row=2, column=0, padx=16, pady=(0, 8), sticky="w")

        self.combo_rate = ctk.CTkOptionMenu(
            config_card,
            values=["48.000 Hz - Alta Fidelidade (Padrão)", "44.100 Hz - Qualidade CD"]
        )
        self.combo_rate.grid(row=2, column=1, columnspan=2, padx=(0, 16), pady=(0, 8), sticky="ew")

        # Pasta de Destino
        lbl_dir = ctk.CTkLabel(config_card, text="Salvar em:", font=ctk.CTkFont(weight="bold"))
        lbl_dir.grid(row=3, column=0, padx=16, pady=(0, 8), sticky="w")

        self.entry_dir = ctk.CTkEntry(config_card, placeholder_text="Caminho da pasta...")
        self.entry_dir.insert(0, self.output_dir)
        self.entry_dir.grid(row=3, column=1, padx=(0, 8), pady=(0, 8), sticky="ew")
        self.entry_dir.bind("<FocusOut>", lambda e: self._on_entry_dir_change())
        self.entry_dir.bind("<Return>", lambda e: self._on_entry_dir_change())

        self.btn_browse = ctk.CTkButton(
            config_card, text="📁 Alterar", width=80, command=self._choose_directory, fg_color="#334155", hover_color="#475569"
        )
        self.btn_browse.grid(row=3, column=2, padx=(0, 16), pady=(0, 8))

        # Opções de Modo Invisível no Meet/Teams e Feedback Sonoro
        switches_frame = ctk.CTkFrame(config_card, fg_color="transparent")
        switches_frame.grid(row=4, column=0, columnspan=3, padx=16, pady=(0, 12), sticky="ew")

        self.var_anti_capture = ctk.BooleanVar(value=True)
        self.switch_anti_capture = ctk.CTkSwitch(
            switches_frame,
            text="Invisível no compartilhamento de tela",
            variable=self.var_anti_capture,
            command=self._update_display_affinity,
            font=ctk.CTkFont(size=12, weight="bold"),
            progress_color="#6366f1"
        )
        self.switch_anti_capture.pack(side="left", padx=(0, 20))

        self.var_sound_feedback = ctk.BooleanVar(value=True)
        self.switch_sound = ctk.CTkSwitch(
            switches_frame,
            text="Bipes discretos nos atalhos",
            variable=self.var_sound_feedback,
            font=ctk.CTkFont(size=12),
            progress_color="#10b981"
        )
        self.switch_sound.pack(side="left", padx=(0, 20))

        self.var_start_hidden = ctk.BooleanVar(value=False)
        self.switch_start_hidden = ctk.CTkSwitch(
            switches_frame,
            text="Iniciar oculto",
            variable=self.var_start_hidden,
            font=ctk.CTkFont(size=12),
            progress_color="#8b5cf6"
        )
        self.switch_start_hidden.pack(side="left")

        # ----------------- PAINEL DO GRAVADOR (STATUS, TIMER & VU METER) -----------------
        monitor_card = ctk.CTkFrame(self, corner_radius=12)
        monitor_card.grid(row=2, column=0, padx=24, pady=10, sticky="ew")
        monitor_card.grid_columnconfigure(0, weight=1)

        # Status badge
        self.lbl_status = ctk.CTkLabel(
            monitor_card,
            text="⚪ PRONTO PARA GRAVAR",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#94a3b8"
        )
        self.lbl_status.pack(pady=(16, 4))

        # Timer Display
        self.lbl_timer = ctk.CTkLabel(
            monitor_card,
            text="00:00:00",
            font=ctk.CTkFont(family="Consolas", size=48, weight="bold")
        )
        self.lbl_timer.pack(pady=4)

        # Volume / VU Meter
        vu_frame = ctk.CTkFrame(monitor_card, fg_color="transparent")
        vu_frame.pack(fill="x", padx=32, pady=(8, 4))

        vu_header_frame = ctk.CTkFrame(vu_frame, fg_color="transparent")
        vu_header_frame.pack(fill="x")

        lbl_vu_title = ctk.CTkLabel(
            vu_header_frame,
            text="Nível de Áudio do Sistema:",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8"
        )
        lbl_vu_title.pack(side="left")

        self.lbl_vu_pct = ctk.CTkLabel(
            vu_header_frame,
            text="0%",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color="#94a3b8"
        )
        self.lbl_vu_pct.pack(side="right")

        self.vu_bar = ctk.CTkProgressBar(vu_frame, height=14, corner_radius=7)
        self.vu_bar.set(0.0)
        self.vu_bar.configure(progress_color="#10b981")
        self.vu_bar.pack(fill="x", pady=(4, 8))

        # Linha informativa de arquivo
        self.lbl_info = ctk.CTkLabel(
            monitor_card,
            text="Tamanho estimado: 0.0 MB | Formato: MP3 (192 kbps - ~88% menor)",
            font=ctk.CTkFont(size=12),
            text_color="#64748b"
        )
        self.lbl_info.pack(pady=(0, 16))

        # Botões de Ação
        actions_frame = ctk.CTkFrame(monitor_card, fg_color="transparent")
        actions_frame.pack(pady=(0, 10))

        self.btn_record = ctk.CTkButton(
            actions_frame,
            text="⏺ Iniciar (Alt+R)",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#e11d48",
            hover_color="#be123c",
            width=150,
            height=42,
            command=self._toggle_record
        )
        self.btn_record.pack(side="left", padx=6)

        self.btn_pause = ctk.CTkButton(
            actions_frame,
            text="⏸ Pausar (Alt+E)",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#d97706",
            hover_color="#b45309",
            width=140,
            height=42,
            state="disabled",
            command=self._toggle_pause
        )
        self.btn_pause.pack(side="left", padx=6)

        self.btn_stop = ctk.CTkButton(
            actions_frame,
            text="⏹ Salvar (Alt+S)",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#475569",
            hover_color="#334155",
            width=140,
            height=42,
            state="disabled",
            command=self._stop_recording
        )
        self.btn_stop.pack(side="left", padx=6)

        self.btn_open_folder = ctk.CTkButton(
            actions_frame,
            text="📁 Pasta",
            font=ctk.CTkFont(size=14),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            width=100,
            height=42,
            command=self._open_output_folder
        )
        self.btn_open_folder.pack(side="left", padx=6)

        # Informação de Atalhos de Teclado
        self.lbl_shortcuts_hint = ctk.CTkLabel(
            monitor_card,
            text="⌨️ Atalhos Globais:  Alt+R Gravar  •  Alt+E / Alt+P Pausar  •  Alt+S Salvar  •  Alt+H Ocultar Janela",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#38bdf8"
        )
        self.lbl_shortcuts_hint.pack(pady=(0, 14))

        # ----------------- HISTÓRICO DE GRAVAÇÕES -----------------
        history_header = ctk.CTkFrame(self, fg_color="transparent")
        history_header.grid(row=3, column=0, padx=24, pady=(10, 4), sticky="ew")

        lbl_hist = ctk.CTkLabel(
            history_header,
            text="Gravações Recentes:",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        lbl_hist.pack(side="left")

        self.scroll_history = ctk.CTkScrollableFrame(self, corner_radius=12, height=140)
        self.scroll_history.grid(row=4, column=0, padx=24, pady=(0, 20), sticky="nsew")
        self.grid_rowconfigure(4, weight=1)

        self._refresh_history_ui()

    def _load_audio_devices(self):
        """Busca todas as saídas de áudio disponíveis."""
        try:
            speakers = sc.all_speakers()
            if not speakers:
                self.combo_devices.configure(values=["Nenhum alto-falante detectado"])
                self.combo_devices.set("Nenhum alto-falante detectado")
                return

            names = [s.name for s in speakers]
            self.combo_devices.configure(values=names)

            # Define o padrão do sistema
            default_spk = sc.default_speaker()
            if default_spk and default_spk.name in names:
                self.combo_devices.set(default_spk.name)
            else:
                self.combo_devices.set(names[0])
        except Exception as e:
            self.combo_devices.configure(values=["Erro ao carregar"])
            messagebox.showerror("Erro de Áudio", f"Erro ao detectar dispositivos de som:\n{e}")

    def _on_format_changed(self, choice):
        """Atualiza a UI e os campos ao alternar entre MP3 e WAV."""
        if "MP3" in choice:
            self.combo_bitrate.configure(state="normal")
            bitrate = self._get_selected_bitrate()
            reduction = "~92%" if bitrate == 128 else ("~88%" if bitrate == 192 else "~75%")
            self.lbl_info.configure(text=f"Tamanho estimado: 0.0 MB | Formato: MP3 ({bitrate} kbps - {reduction} menor)")
        else:
            self.combo_bitrate.configure(state="disabled")
            self.lbl_info.configure(text="Tamanho estimado: 0.0 MB | Formato: WAV PCM 16-bit Estéreo (Sem compressão)")

    def _on_bitrate_changed(self, choice):
        """Atualiza o texto descritivo ao mudar o bitrate do MP3."""
        if "MP3" in self.combo_format.get():
            bitrate = self._get_selected_bitrate()
            reduction = "~92%" if bitrate == 128 else ("~88%" if bitrate == 192 else "~75%")
            self.lbl_info.configure(text=f"Tamanho estimado: 0.0 MB | Formato: MP3 ({bitrate} kbps - {reduction} menor)")

    def _get_selected_bitrate(self):
        """Retorna o bitrate numérico em kbps selecionado na interface."""
        val = self.combo_bitrate.get()
        if "128" in val:
            return 128
        elif "320" in val:
            return 320
        return 192

    @staticmethod
    def _convert_wav_to_mp3(wav_path, mp3_path, bitrate_kbps=192):
        """Converte WAV para MP3 usando FFmpeg com fallback automático para soundfile."""
        import subprocess
        # 1. Tenta FFmpeg nativo (ultrarrápido com libmp3lame)
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            cmd = [
                "ffmpeg", "-y",
                "-i", wav_path,
                "-c:a", "libmp3lame",
                "-b:a", f"{bitrate_kbps}k",
                mp3_path
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                timeout=180
            )
            if res.returncode == 0 and os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                return True
        except Exception as e:
            print(f"Aviso na conversão FFmpeg: {e}")

        # 2. Fallback via soundfile nativo
        try:
            import soundfile as sf
            data, sr = sf.read(wav_path)
            sf.write(mp3_path, data, sr, format="MP3")
            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                return True
        except Exception as e:
            print(f"Aviso no fallback soundfile: {e}")

        return False

    def _compress_wav_file(self, wav_path):
        """Comprime um arquivo WAV existente na lista de histórico para MP3."""
        if not os.path.exists(wav_path):
            messagebox.showwarning("Aviso", "O arquivo não foi encontrado.")
            return

        base_name = os.path.splitext(wav_path)[0]
        mp3_path = base_name + ".mp3"

        if os.path.exists(mp3_path):
            if not messagebox.askyesno("Substituir", f"O arquivo '{os.path.basename(mp3_path)}' já existe.\nDeseja substituí-lo?"):
                return

        bitrate = self._get_selected_bitrate()
        orig_size_mb = os.path.getsize(wav_path) / (1024 * 1024)

        self._is_compressing = True
        self.lbl_status.configure(text=f"⚡ COMPRIMINDO {os.path.basename(wav_path)}...", text_color="#38bdf8")

        def _worker():
            success = self._convert_wav_to_mp3(wav_path, mp3_path, bitrate)
            self._is_compressing = False
            if success:
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
                self.after(0, lambda: self._on_compress_wav_finished(mp3_path, orig_size_mb, True))
            else:
                self.after(0, lambda: self._on_compress_wav_finished(wav_path, orig_size_mb, False))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_compress_wav_finished(self, final_path, orig_size_mb, success):
        self.lbl_status.configure(text="⚪ PRONTO PARA GRAVAR", text_color="#94a3b8")
        if success and os.path.exists(final_path):
            new_size_mb = os.path.getsize(final_path) / (1024 * 1024)
            pct_saved = ((orig_size_mb - new_size_mb) / orig_size_mb) * 100 if orig_size_mb > 0 else 0
            self.lbl_info.configure(text=f"Comprimido: {os.path.basename(final_path)} ({new_size_mb:.2f} MB, economia de {pct_saved:.0f}%)")
            self._refresh_history_ui()
            messagebox.showinfo(
                "Compressão Concluída",
                f"Gravação comprimida com sucesso para MP3!\n\n"
                f"• Tamanho original (WAV): {orig_size_mb:.2f} MB\n"
                f"• Novo tamanho (MP3): {new_size_mb:.2f} MB\n"
                f"• Espaço economizado: {pct_saved:.1f}%"
            )
        else:
            messagebox.showerror("Erro de Compressão", "Não foi possível converter o arquivo para MP3.")

    def _bind_shortcuts(self):
        """Captura os atalhos com a janela em foco de forma robusta e sem conflitos."""
        # Usa bindings específicos para cada combinação Alt+tecla.
        # O bind_all("<Key>") genérico é pouco confiável com Alt no Windows
        # porque o Tkinter nem sempre reporta o keysym/state de forma consistente.
        # Bindings explícitos como <Alt-r> funcionam de forma nativa e confiável.

        for key_variant in ("r", "R"):
            self.bind_all(f"<Alt-{key_variant}>", self._shortcut_record)
            self.bind_all(f"<Alt-KeyPress-{key_variant}>", self._shortcut_record)

        for key_variant in ("e", "E", "p", "P"):
            self.bind_all(f"<Alt-{key_variant}>", self._shortcut_pause)
            self.bind_all(f"<Alt-KeyPress-{key_variant}>", self._shortcut_pause)

        for key_variant in ("s", "S"):
            self.bind_all(f"<Alt-{key_variant}>", self._shortcut_stop)
            self.bind_all(f"<Alt-KeyPress-{key_variant}>", self._shortcut_stop)

        for key_variant in ("h", "H"):
            self.bind_all(f"<Alt-{key_variant}>", self._shortcut_hide)
            self.bind_all(f"<Alt-KeyPress-{key_variant}>", self._shortcut_hide)

    def _shortcut_record(self, event=None):
        """Atalho Alt+R - Iniciar gravação."""
        if event and event.widget == self.entry_dir:
            return
        self._hotkey_record()
        return "break"

    def _shortcut_pause(self, event=None):
        """Atalho Alt+E / Alt+P - Pausar/Retomar gravação."""
        if event and event.widget == self.entry_dir:
            return
        self._hotkey_pause()
        return "break"

    def _shortcut_stop(self, event=None):
        """Atalho Alt+S - Salvar/Parar gravação."""
        if event and event.widget == self.entry_dir:
            return
        self._hotkey_stop()
        return "break"

    def _shortcut_hide(self, event=None):
        """Atalho Alt+H - Ocultar/Mostrar janela."""
        if event and event.widget == self.entry_dir:
            return
        self._toggle_window_visibility()
        return "break"

    def _register_global_hotkeys(self):
        """Registra atalhos globais no Windows usando Win32 RegisterHotKey API.
        Funciona mesmo quando a janela está oculta (withdraw), sem dependências externas."""
        if sys.platform != "win32":
            return

        self._hotkey_thread_id = None

        def _hotkey_listener():
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            MOD_ALT = 0x0001
            MOD_NOREPEAT = 0x4000

            # Mapa: ID -> (modificadores, tecla virtual, callback)
            hotkey_map = {
                1: (MOD_ALT | MOD_NOREPEAT, 0x52, self._hotkey_record),              # Alt+R
                2: (MOD_ALT | MOD_NOREPEAT, 0x45, self._hotkey_pause),                # Alt+E
                3: (MOD_ALT | MOD_NOREPEAT, 0x50, self._hotkey_pause),                # Alt+P
                4: (MOD_ALT | MOD_NOREPEAT, 0x53, self._hotkey_stop),                 # Alt+S
                5: (MOD_ALT | MOD_NOREPEAT, 0x48, self._toggle_window_visibility),    # Alt+H
            }

            for hk_id, (mod, vk, _) in hotkey_map.items():
                try:
                    user32.RegisterHotKey(None, hk_id, mod, vk)
                except Exception:
                    pass

            # Armazena o ID da thread para poder encerrá-la no _on_close
            self._hotkey_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

            msg = ctypes.wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:  # WM_HOTKEY
                    hk_id = msg.wParam
                    if hk_id in hotkey_map:
                        callback = hotkey_map[hk_id][2]
                        try:
                            self.after(0, callback)
                        except Exception:
                            pass

            # Cleanup ao encerrar
            for hk_id in hotkey_map:
                try:
                    user32.UnregisterHotKey(None, hk_id)
                except Exception:
                    pass

        self._hotkey_thread = threading.Thread(target=_hotkey_listener, daemon=True)
        self._hotkey_thread.start()

    def _toggle_window_visibility(self):
        """Alterna entre ocultar e exibir a janela completamente da tela e da barra de tarefas."""
        if self.winfo_viewable():
            self.withdraw()
            self._play_feedback_sound("hide")
        else:
            self.deiconify()
            self.lift()
            self.focus_force()
            self._update_display_affinity()
            self._play_feedback_sound("show")

    def _update_display_affinity(self):
        """Torna a janela invisível em compartilhamento de tela do Meet/Teams/Discord via Win32 API."""
        if sys.platform != "win32":
            return
        try:
            hwnd1 = self.winfo_id()
            hwnd2 = ctypes.windll.user32.GetParent(hwnd1)
            target_hwnd = hwnd2 if hwnd2 else hwnd1

            # 0x00000011 (WDA_EXCLUDEFROMCAPTURE) remove a janela da gravação/compartilhamento de tela
            # 0x00000000 (WDA_NONE) volta a exibir normalmente na captura
            affinity = 0x00000011 if self.var_anti_capture.get() else 0x00000000
            ctypes.windll.user32.SetWindowDisplayAffinity(target_hwnd, affinity)
        except Exception as e:
            print(f"Aviso ao configurar afinidade de janela: {e}")

    def _play_feedback_sound(self, sound_type):
        """Toca um bipe discreto em thread separada para avisar o usuário mesmo quando a janela estiver oculta."""
        if not self.var_sound_feedback.get() or sys.platform != "win32":
            return

        def _sound_worker():
            import winsound
            try:
                if sound_type == "start":
                    winsound.Beep(1000, 100)
                elif sound_type == "pause":
                    winsound.Beep(650, 120)
                elif sound_type == "resume":
                    winsound.Beep(1000, 80)
                elif sound_type == "stop":
                    winsound.Beep(1100, 80)
                    winsound.Beep(1400, 100)
                elif sound_type == "hide":
                    winsound.Beep(500, 80)
                elif sound_type == "show":
                    winsound.Beep(800, 80)
            except Exception:
                pass

        threading.Thread(target=_sound_worker, daemon=True).start()

    def _hotkey_record(self):
        if not self.engine.is_recording:
            self._toggle_record()

    def _hotkey_pause(self):
        if not self.engine.is_recording:
            # Feedback visual caso o usuário aperte Alt+E antes de iniciar a gravação
            self.lbl_status.configure(text="⚠️ Inicie a gravação com Alt+R primeiro", text_color="#f59e0b")
            self.after(2000, lambda: self.lbl_status.configure(text="⚪ PRONTO PARA GRAVAR", text_color="#94a3b8") if not self.engine.is_recording else None)
            return

        self._toggle_pause()

    def _hotkey_stop(self):
        if self.engine.is_recording:
            self._stop_recording()

    def _on_entry_dir_change(self):
        """Permite que o usuário digite ou cole um caminho manualmente."""
        text = self.entry_dir.get().strip()
        if text:
            try:
                os.makedirs(text, exist_ok=True)
                self.output_dir = os.path.normpath(text)
                self._refresh_history_ui()
            except Exception:
                pass

    def _choose_directory(self):
        """Abre seletor de pastas de forma 100% segura contra crashes de COM."""
        new_dir = None

        # 1. Tenta o seletor nativo do Tkinter ancorado à janela
        try:
            new_dir = filedialog.askdirectory(
                parent=self,
                initialdir=self.output_dir,
                title="Escolha a pasta para salvar as gravações"
            )
        except Exception as e:
            print(f"Aviso ao abrir seletor padrão: {e}")
            new_dir = None

        # 2. Se falhar ou retornar vazio/nulo por erro interno, aciona o FolderBrowserDialog do Windows
        if not new_dir:
            try:
                new_dir = self._ask_directory_windows(self.output_dir)
            except Exception as e:
                print(f"Aviso no seletor Windows: {e}")
                new_dir = None

        if new_dir and os.path.exists(new_dir):
            self.output_dir = os.path.normpath(new_dir)
            self.entry_dir.delete(0, "end")
            self.entry_dir.insert(0, self.output_dir)
            self._refresh_history_ui()

    def _ask_directory_windows(self, initialdir):
        """Fallback via FolderBrowserDialog nativo do Windows Forms."""
        import subprocess
        initialdir_clean = os.path.abspath(initialdir).replace("'", "''")
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Add-Type -AssemblyName System.Windows.Forms; "
            f"$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
            f"$f.SelectedPath = '{initialdir_clean}'; "
            f"$f.Description = 'Escolha a pasta para salvar as gravações'; "
            f"$f.ShowNewFolderButton = $true; "
            f"if ($f.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{ Write-Output $f.SelectedPath }}"
        ]
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            startupinfo=startupinfo,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        out = res.stdout.strip()
        return out if out and os.path.exists(out) else None

    def _toggle_record(self):
        if not self.engine.is_recording:
            # Iniciar gravação
            speaker_name = self.combo_devices.get()
            if not speaker_name or "Nenhum" in speaker_name:
                messagebox.showwarning("Aviso", "Selecione um dispositivo de áudio válido.")
                return

            samplerate = 48000 if "48.000" in self.combo_rate.get() else 44100

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            is_mp3 = "MP3" in self.combo_format.get()
            self.current_is_mp3 = is_mp3
            self.current_bitrate = self._get_selected_bitrate()

            if is_mp3:
                self.final_recording_file = os.path.join(self.output_dir, f"audio_pc_{timestamp}.mp3")
                temp_filename = f"audio_pc_{timestamp}.temp.wav"
                filepath = os.path.join(self.output_dir, temp_filename)
            else:
                self.final_recording_file = os.path.join(self.output_dir, f"audio_pc_{timestamp}.wav")
                filepath = self.final_recording_file

            self.current_recording_file = filepath

            self.lbl_status.configure(text="🔴 GRAVANDO ÁUDIO DO PC...", text_color="#ef4444")
            self.btn_record.configure(state="disabled")
            self.btn_pause.configure(state="normal", text="⏸ Pausar (Alt+E)")
            self.btn_stop.configure(state="normal")
            self.combo_devices.configure(state="disabled")
            self.btn_refresh_dev.configure(state="disabled")
            self.combo_rate.configure(state="disabled")
            self.combo_format.configure(state="disabled")
            self.combo_bitrate.configure(state="disabled")

            self.engine.start(speaker_name, filepath, samplerate)
            self._play_feedback_sound("start")
        else:
            pass

    def _toggle_pause(self):
        if self.engine.is_recording:
            if not self.engine.is_paused:
                self.engine.pause()
                self._play_feedback_sound("pause")
                self.lbl_status.configure(text="⏸️ GRAVAÇÃO PAUSADA", text_color="#f59e0b")
                self.btn_pause.configure(text="▶ Retomar (Alt+E)", fg_color="#10b981", hover_color="#059669")
                self.vu_bar.set(0.0)
                self.lbl_vu_pct.configure(text="0%")
            else:
                self.engine.resume()
                self._play_feedback_sound("resume")
                self.lbl_status.configure(text="🔴 GRAVANDO ÁUDIO DO PC...", text_color="#ef4444")
                self.btn_pause.configure(text="⏸ Pausar (Alt+E)", fg_color="#d97706", hover_color="#b45309")

    def _stop_recording(self):
        if self.engine.is_recording:
            self._play_feedback_sound("stop")
            if getattr(self, "current_is_mp3", False):
                self.lbl_status.configure(text="⚡ COMPRIMINDO PARA MP3...", text_color="#38bdf8")
            else:
                self.lbl_status.configure(text="⏳ SALVANDO ARQUIVO...", text_color="#38bdf8")
            self.btn_stop.configure(state="disabled")
            self.btn_pause.configure(state="disabled")

            # Para o motor em background
            threading.Thread(target=self._async_stop, daemon=True).start()

    def _async_stop(self):
        self.engine.stop()

    def _safe_volume_update(self, rms):
        # Atualiza a UI na thread do Tkinter
        self.after(0, self._update_volume_ui, rms)

    def _update_volume_ui(self, rms):
        # Escala não-linear para sensibilidade humana ao volume
        # rms típico fica entre 0.0001 e 0.5
        scaled = min(1.0, float(np.sqrt(rms * 4.0)))
        pct = int(scaled * 100)

        self.vu_bar.set(scaled)
        self.lbl_vu_pct.configure(text=f"{pct}%")

        if scaled > 0.85:
            self.vu_bar.configure(progress_color="#ef4444")  # Vermelho (pico)
        elif scaled > 0.6:
            self.vu_bar.configure(progress_color="#f59e0b")  # Amarelo
        else:
            self.vu_bar.configure(progress_color="#10b981")  # Verde saudável

    def _safe_time_update(self, seconds, total_frames):
        self.after(0, self._update_time_ui, seconds, total_frames)

    def _update_time_ui(self, seconds, total_frames):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        self.lbl_timer.configure(text=f"{h:02d}:{m:02d}:{s:02d}")

        if getattr(self, "current_is_mp3", False):
            # MP3 estimado com base no bitrate selecionado (kbps * 1000 / 8 bytes/s)
            bitrate = getattr(self, "current_bitrate", 192)
            bytes_est = seconds * (bitrate * 1000 / 8)
            mb = bytes_est / (1024 * 1024)
            self.lbl_info.configure(text=f"Tamanho estimado: {mb:.2f} MB | Formato: MP3 ({bitrate} kbps Estéreo)")
        else:
            # Tamanho estimado em MB (16-bit 2 canais = 4 bytes por frame)
            bytes_est = total_frames * 4
            mb = bytes_est / (1024 * 1024)
            self.lbl_info.configure(text=f"Tamanho estimado: {mb:.2f} MB | Formato: WAV PCM 16-bit Estéreo")

    def _safe_finished(self, filepath):
        # Se for gravação em MP3, converte em thread de background antes de atualizar a UI
        if getattr(self, "current_is_mp3", False) and filepath and os.path.exists(filepath):
            self._is_compressing = True
            self.lbl_status.configure(text="⚡ COMPRIMINDO PARA MP3...", text_color="#38bdf8")

            def _convert_worker():
                final_path = self.final_recording_file
                orig_size_mb = os.path.getsize(filepath) / (1024 * 1024)
                success = self._convert_wav_to_mp3(filepath, final_path, getattr(self, "current_bitrate", 192))
                self._is_compressing = False
                if success and os.path.exists(final_path):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                    actual_file = final_path
                else:
                    actual_file = filepath

                self.after(0, self._on_recording_finished, actual_file)

            threading.Thread(target=_convert_worker, daemon=True).start()
        else:
            self.after(0, self._on_recording_finished, filepath)

    def _on_recording_finished(self, filepath):
        self.lbl_status.configure(text="⚪ PRONTO PARA GRAVAR", text_color="#94a3b8")
        self.btn_record.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="⏸ Pausar (Alt+E)", fg_color="#d97706", hover_color="#b45309")
        self.btn_stop.configure(state="disabled")
        self.combo_devices.configure(state="normal")
        self.btn_refresh_dev.configure(state="normal")
        self.combo_rate.configure(state="normal")
        self.combo_format.configure(state="normal")
        if "MP3" in self.combo_format.get():
            self.combo_bitrate.configure(state="normal")
        else:
            self.combo_bitrate.configure(state="disabled")

        self.vu_bar.set(0.0)
        self.lbl_vu_pct.configure(text="0%")

        # Tamanho final real
        if filepath and os.path.exists(filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            self.lbl_info.configure(text=f"Última gravação salva: {os.path.basename(filepath)} ({size_mb:.2f} MB)")
            self._refresh_history_ui()

    def _safe_error(self, err_msg):
        self.after(0, self._on_recording_error, err_msg)

    def _on_recording_error(self, err_msg):
        self._on_recording_finished("")
        messagebox.showerror("Erro na Gravação", f"Ocorreu um erro durante a gravação:\n{err_msg}")

    def _open_output_folder(self):
        try:
            os.startfile(self.output_dir)
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível abrir a pasta:\n{e}")

    def _play_audio_file(self, filepath):
        try:
            if os.path.exists(filepath):
                os.startfile(filepath)
            else:
                messagebox.showwarning("Aviso", "O arquivo não foi encontrado.")
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível reproduzir o arquivo:\n{e}")

    def _rename_audio_file(self, filepath):
        """Abre um diálogo para renomear o arquivo de gravação preservando sua extensão."""
        if not os.path.exists(filepath):
            messagebox.showwarning("Aviso", "O arquivo não foi encontrado.")
            return

        old_name = os.path.basename(filepath)
        old_name_no_ext, ext = os.path.splitext(old_name)

        dialog = ctk.CTkInputDialog(
            text=f"Novo nome para o arquivo:\n(sem extensão {ext})",
            title="Renomear Gravação"
        )
        new_name = dialog.get_input()

        if not new_name or new_name.strip() == "":
            return

        new_name = new_name.strip()
        # Remove caracteres inválidos para nomes de arquivo no Windows
        invalid_chars = '<>:"/\\|?*'
        for ch in invalid_chars:
            new_name = new_name.replace(ch, "_")

        if not new_name.lower().endswith(ext.lower()):
            new_name += ext

        new_path = os.path.join(os.path.dirname(filepath), new_name)

        if os.path.exists(new_path):
            messagebox.showwarning("Aviso", f"Já existe um arquivo com o nome '{new_name}'.")
            return

        try:
            os.rename(filepath, new_path)
            self._refresh_history_ui()
        except Exception as e:
            messagebox.showerror("Erro", f"Não foi possível renomear o arquivo:\n{e}")

    def _start_hidden(self):
        """Oculta a janela na inicialização."""
        self.withdraw()
        self._play_feedback_sound("hide")

    def _refresh_history_ui(self):
        # Limpa widgets antigos do scroll
        for widget in self.scroll_history.winfo_children():
            widget.destroy()

        if not os.path.exists(self.output_dir):
            return

        # Pega os 10 arquivos de áudio mais recentes da pasta (.mp3 e .wav)
        try:
            files = [
                f for f in os.listdir(self.output_dir)
                if f.lower().endswith((".mp3", ".wav")) and not f.lower().endswith(".temp.wav")
            ]
            files.sort(key=lambda x: os.path.getmtime(os.path.join(self.output_dir, x)), reverse=True)
            files = files[:10]
        except Exception:
            files = []

        if not files:
            empty_lbl = ctk.CTkLabel(
                self.scroll_history,
                text="Nenhuma gravação encontrada nesta pasta.",
                text_color="#64748b",
                font=ctk.CTkFont(size=12)
            )
            empty_lbl.pack(pady=20)
            return

        for fname in files:
            full_path = os.path.join(self.output_dir, fname)
            size_mb = os.path.getsize(full_path) / (1024 * 1024)
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%d/%m/%Y %H:%M")
            is_wav = fname.lower().endswith(".wav")
            fmt_tag = "WAV (Original)" if is_wav else "MP3 (Compacto)"
            tag_color = "#f59e0b" if is_wav else "#10b981"

            item_frame = ctk.CTkFrame(self.scroll_history, fg_color="#1e293b", corner_radius=8)
            item_frame.pack(fill="x", pady=3, padx=2)

            info_subframe = ctk.CTkFrame(item_frame, fg_color="transparent")
            info_subframe.pack(side="left", padx=12, pady=6, fill="x", expand=True)

            name_lbl = ctk.CTkLabel(
                info_subframe,
                text=fname,
                font=ctk.CTkFont(size=12, weight="bold"),
                anchor="w"
            )
            name_lbl.pack(anchor="w")

            meta_lbl = ctk.CTkLabel(
                info_subframe,
                text=f"{mtime}  •  {size_mb:.2f} MB  •  {fmt_tag}",
                font=ctk.CTkFont(size=11),
                text_color=tag_color,
                anchor="w"
            )
            meta_lbl.pack(anchor="w")

            play_btn = ctk.CTkButton(
                item_frame,
                text="▶ Ouvir",
                width=65,
                height=28,
                font=ctk.CTkFont(size=12),
                fg_color="#3b82f6",
                hover_color="#1d4ed8",
                command=lambda p=full_path: self._play_audio_file(p)
            )
            play_btn.pack(side="right", padx=(4, 10), pady=6)

            rename_btn = ctk.CTkButton(
                item_frame,
                text="✏️ Renomear",
                width=85,
                height=28,
                font=ctk.CTkFont(size=12),
                fg_color="#475569",
                hover_color="#334155",
                command=lambda p=full_path: self._rename_audio_file(p)
            )
            rename_btn.pack(side="right", padx=(4, 0), pady=6)

            # Se for WAV, oferece o botão rápido de compressão para liberar espaço
            if is_wav:
                compress_btn = ctk.CTkButton(
                    item_frame,
                    text="⚡ Comprimir",
                    width=90,
                    height=28,
                    font=ctk.CTkFont(size=12, weight="bold"),
                    fg_color="#059669",
                    hover_color="#047857",
                    command=lambda p=full_path: self._compress_wav_file(p)
                )
                compress_btn.pack(side="right", padx=(4, 0), pady=6)

    def _on_close(self):
        # Encerra a thread de hotkeys globais enviando WM_QUIT
        if hasattr(self, '_hotkey_thread_id') and self._hotkey_thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(
                    self._hotkey_thread_id, 0x0012, 0, 0  # WM_QUIT
                )
            except Exception:
                pass

        if getattr(self, "_is_compressing", False):
            if not messagebox.askyesno("Compressão em andamento", "Uma compressão de áudio ainda está em execução.\nTem certeza que deseja fechar agora?"):
                return

        if self.engine.is_recording:
            if messagebox.askyesno("Gravação em andamento", "Deseja parar e salvar a gravação antes de sair?"):
                self.engine.stop()
            else:
                self.engine.stop()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
