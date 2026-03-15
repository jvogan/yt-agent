# Shell Completion

`yt-agent` uses Typer's built-in shell completion installer. Run the install command from the shell you want to configure, because `yt-agent --install-completion` installs completion for the current shell only.

## Common install flow

Run this from the shell you want to enable:

```bash
yt-agent --install-completion
```

After it finishes:

- Restart the shell, or reload the relevant startup file.
- Re-run the install command after reinstalling `yt-agent` or switching shells.
- Keep using explicit commands in scripts and docs. Completion is for interactive use only.

## Bash

Run the installer from a `bash` session:

```bash
yt-agent --install-completion
```

Typer writes:

- `~/.bash_completions/yt-agent.sh`
- a `source` line in `~/.bashrc` that points at that file

Verify the install:

```bash
test -f ~/.bash_completions/yt-agent.sh && echo OK
grep -F ".bash_completions/yt-agent.sh" ~/.bashrc
source ~/.bashrc
complete -p yt-agent
```

`complete -p yt-agent` should print a completion definition for `yt-agent`.

## Zsh

Run the installer from a `zsh` session:

```bash
yt-agent --install-completion
```

Typer writes:

- `~/.zfunc/_yt-agent`
- `fpath+=~/.zfunc; autoload -Uz compinit; compinit` in `~/.zshrc` if it is not already present
- `zstyle ':completion:*' menu select` in `~/.zshrc` only when your file does not already define a `zstyle`

Verify the install:

```bash
test -f ~/.zfunc/_yt-agent && echo OK
grep -F "fpath+=~/.zfunc; autoload -Uz compinit; compinit" ~/.zshrc
autoload -Uz compinit && compinit
whence -w _yt-agent
```

`whence -w _yt-agent` should report `function`.

## Fish

Run the installer from a `fish` session:

```bash
yt-agent --install-completion
```

Typer writes:

- `~/.config/fish/completions/yt-agent.fish`

Fish loads completion files from that directory automatically in new shells.

Verify the install:

```bash
test -f ~/.config/fish/completions/yt-agent.fish && echo OK
complete --do-complete "yt-agent do"
```

`complete --do-complete "yt-agent do"` should print matching completions such as `doctor`.

## Troubleshooting

- Install from the target shell. Running `yt-agent --install-completion` from `zsh` configures `zsh`, not `bash` or `fish`.
- If the shell restarts but completion still does not load, inspect the generated script with `yt-agent --show-completion` from that same shell.
- If `yt-agent` is not found at install time, fix your PATH first and confirm with `yt-agent doctor`.
- If Bash still does not load completion after install, your terminal may be starting Bash as a login shell that skips `~/.bashrc`. Add `[[ -f ~/.bashrc ]] && source ~/.bashrc` to `~/.bash_profile`, then start a new shell.
- If Zsh already manages `compinit` through a framework or dotfiles setup, keep `~/.zfunc` in `fpath` and avoid adding a second completion init path by hand.
- If Fish wrote the file but completion still looks stale, start a fresh shell with `exec fish`.

## Related docs

- Install and first-run guide: [getting-started.md](getting-started.md)
- Command list: [command-reference.md](command-reference.md)
- Quick recipes: [recipes.md](recipes.md)
- Common questions: [faq.md](faq.md)
