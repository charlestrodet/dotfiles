# Dotfiles

Configuration Kitty + tmux + fish, prévue pour Ubuntu sous WSL2, Debian et Fedora. Kitty lance fish, puis fish attache une session tmux distincte à chaque onglet Kitty. Les sous-onglets et les splits sont gérés par tmux.

## Installation

Prévoir `git`, `kitty`, `tmux` et `fish`. Le script ne les installe pas et ne change pas le shell de connexion. Une fois le dépôt privé créé sur GitHub et l'accès SSH configuré :

Sur Debian, installer les prérequis avec `sudo apt install git kitty tmux fish`. Sur Fedora, utiliser `sudo dnf install git kitty tmux fish`. Kitty nécessite une session graphique ; l'installateur fonctionne aussi sans en lancer une.

```sh
git clone git@github.com:charlestrodet/dotfiles.git ~/repos/dotfiles
cd ~/repos/dotfiles
./install.sh --dry-run
./install.sh
```

Le script crée des liens symboliques depuis le dossier personnel vers les cinq fichiers versionnés dans `home/`. Il sauvegarde les fichiers déjà présents avec un suffixe `.backup-AAAAmmjj-HHMMSS`. Les modifications des fichiers suivis sont visibles après un `git pull` ; relancer le script si de nouveaux fichiers sont ajoutés ou si le dépôt change de place. `--dry-run` affiche les actions sans modifier les fichiers.

Pour appliquer les changements à une fenêtre Kitty déjà ouverte, recharger Kitty avec `Ctrl+Shift+F5`, puis sourcer tmux avec `Ctrl+Alt+F5`. Les nouvelles sessions prennent la configuration automatiquement.

## Raccourcis principaux

| Action | Raccourci |
| --- | --- |
| Nouvel onglet Kitty | `Ctrl+Shift+T` |
| Onglet Kitty précédent / suivant | `Ctrl+[` / `Ctrl+]` |
| Nouveau sous-onglet tmux | `Ctrl+A`, puis `c` |
| Sous-onglet tmux précédent / suivant | `Alt+[` / `Alt+]` |
| Déplacer le sous-onglet et suivre le focus | `Alt+Shift+[` / `Alt+Shift+]` |
| Fermer le sous-onglet tmux | `Ctrl+Shift+W` |
| Naviguer entre les panes | `Ctrl+Flèches` |
| Split sous / à droite | `Ctrl+Shift+Entrée` / `Ctrl+Shift+|` |
| Déplacer le pane et suivre le focus | `Ctrl+Shift+Flèches` |
| Zoom du pane | `Ctrl+Shift+F` |
| Égaliser les panes | `Ctrl+A`, puis `=` |

Les combinaisons `|` et `Alt+Shift+[` / `]` ont été réglées pour le clavier actuel sous WSLg. Les tester dans Kitty sur un autre clavier ou serveur graphique.

## Contenu et limites

Le dépôt contient le thème Dracula de Kitty avec son attribution dans `current-theme.conf`. Les historiques, `fish_variables`, les clés et les sauvegardes locales restent hors du dépôt. La persistance tmux dure tant que le serveur tmux continue de tourner ; un redémarrage complet de WSL ou du système termine ses processus.
