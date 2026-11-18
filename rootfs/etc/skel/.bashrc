case $- in *i*) ;; *) return ;; esac
PS1='\[\e[38;5;37m\]\u\[\e[0m\]@\h \[\e[38;5;110m\]\w\[\e[0m\] \$ '
alias ls='ls --color=auto'
alias ll='ls -lah'
alias grep='grep --color=auto'
[ -r /usr/share/bash-completion/bash_completion ] && . /usr/share/bash-completion/bash_completion
