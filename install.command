#!/bin/sh
# macOS: duplo clique (na primeira vez, clique com o botão direito > Abrir).
cd "$(dirname "$0")"
sh ./install.sh "$@"
echo
printf "Enter para fechar / press Enter to close "
read _
