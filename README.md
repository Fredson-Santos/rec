# 🎙️ Gravador de Áudio Interno do PC (WASAPI Loopback)

Aplicativo em Python com interface gráfica (Tkinter) para gravar **exclusivamente o áudio do computador** (o som que sai nos alto-falantes/fones, como reuniões no Teams/Meet, vídeos do YouTube, chamadas do Discord, jogos ou qualquer som do sistema), **sem capturar o microfone externo**.

---

## 👻 Modo Invisível em Reuniões (Teams & Meet)

O aplicativo conta com **duas camadas de invisibilidade**:

1. **Anti-Captura de Tela (Win32 Affinity)**:
   - O aplicativo ativa a API nativa do Windows (`WDA_EXCLUDEFROMCAPTURE`).
   - Mesmo que a janela do gravador esteja **aberta no meio da sua tela** enquanto você compartilha a tela inteira no **Teams, Google Meet ou Discord**, os outros participantes **não enxergam a janela**! Para eles, ela é 100% transparente e exibe o que estiver atrás.
2. **Ocultar Janela Totalmente (`Alt + H`)**:
   - Pressione **`Alt + H`** e a janela desaparece completamente da tela e da barra de tarefas.
   - Pressione **`Alt + H`** novamente para trazê-la de volta.
3. **Bipes Sonoros Discretos**:
   - Quando você usa os atalhos com a janela oculta, o programa emite bipes discretos no fone para você saber se iniciou, pausou ou salvou a gravação com certeza.

---

## ⌨️ Atalhos de Teclado (Globais)

Os atalhos funcionam **mesmo com o aplicativo em segundo plano ou oculto**:

| Atalho | Ação | Descrição | Feedback Sonoro |
| :---: | :---: | :--- | :---: |
| **`Alt + R`** | **Começar** | Inicia a gravação imediatamente | Bipe agudo curto |
| **`Alt + E`** ou **`Alt + P`** | **Pausar / Retomar** | Alterna entre pausar e continuar | Bipe médio |
| **`Alt + S`** | **Salvar** | Finaliza e salva o áudio `.wav` | Bipe duplo de sucesso |
| **`Alt + H`** | **Ocultar / Mostrar** | Esconde a janela da tela e da barra de tarefas | Bipe suave |

---

## 🚀 Como Executar

### Opção 1: Pelo executável rápido
Basta dar um duplo clique no arquivo:
- `iniciar.bat`

### Opção 2: Pelo Terminal
```bash
python app.py
```

---

## 🛠️ Tecnologias e Bibliotecas Utilizadas

- **WASAPI Loopback (Windows Audio Session API)**: Captura de áudio de alta fidelidade sem acessar microfones físicos.
- **Win32 SetWindowDisplayAffinity**: Impede que a janela seja gravada ou vista em compartilhamentos de tela.
- **`soundcard`**, **`soundfile`** & **`numpy`**: Gravação contínua direta em disco (.WAV 16-bit 48 kHz).
- **`keyboard`**: Atalhos globais no Windows.
- **`customtkinter`**: Interface moderna com tema escuro e controles intuitivos.

---

## 🗜️ Compressão de Áudio (MP3 & Redução de ~90%)

O RECapp grava áudio com alta fidelidade e permite salvar diretamente em formato ultracompacto:

- **MP3 - Compacto (~90% menor)** *(Padrão Recomendado)*:
  - **192 kbps**: Redução de **~88%** do tamanho com fidelidade quase idêntica ao original. Uma hora de gravação cai de **~700 MB** para apenas **~80 MB**!
  - **128 kbps**: Redução de **mais de 90%**, gerando arquivos leves (~55 MB/hora), ideal para reuniões, palestras e longas chamadas.
  - **320 kbps**: Fidelidade máxima de MP3 com ~75% de economia.
- **WAV - Original Sem Perdas**:
  - Salva em PCM 16-bit estéreo não comprimido para estúdio e edição bruta.
- **⚡ Botão 'Comprimir' no Histórico**:
  - Se você já tem gravações antigas em formato `.wav` ocupando espaço na pasta, clique no botão **`⚡ Comprimir`** ao lado do arquivo no histórico para convertê-lo imediatamente em MP3 e liberar espaço em disco!

---

## 📁 Onde ficam salvas as gravações?

Por padrão, os áudios são salvos na sua pasta de Música:
`C:\Users\<SeuUsuario>\Music\GravacoesPC`

Você pode alterar a pasta a qualquer momento diretamente pela interface do aplicativo no botão **"📁 Alterar"** e clicar em **"📁 Pasta"** para abrir o Explorador de Arquivos na pasta das gravações.
