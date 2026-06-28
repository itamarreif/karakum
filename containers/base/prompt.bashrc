
# karakum agent prompt: `<agent>:<path> √|?code $`. Same shape as the host
# prompt but the name is yellow (host is green) so container ≠ host at a glance.
# Appended last so it wins over the skel default. `\u` is the agent (the unix
# user is renamed to it by the image entrypoint); `\w` is the ~-relative path;
# the `$(…)` shows green (√) on success or red (?<exit-code>) on failure. The
# status color uses raw \001/\002 (the bytes behind \[ \]) since bash does not
# re-process \[ \] that comes out of a command substitution.
PS1='\[\e[1;33m\]\u\[\e[0m\]:\[\e[1;34m\]\w\[\e[0m\] $(c=$?; if [ "$c" = 0 ]; then printf "\001\033[32m\002(√)\001\033[0m\002"; else printf "\001\033[31m\002(?%s)\001\033[0m\002" "$c"; fi) \$ '
