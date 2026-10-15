# Dotfiles

Configuration Kitty + tmux pour Linux, notamment Debian, Fedora et Ubuntu. La première instance Kitty ouverte utilise les projets et tmux ; les suivantes ouvrent des terminaux simples avec le shell de la machine. Une configuration de prompt fish est fournie si fish est le shell choisi.

## Installation

Installer `git`, `kitty`, `tmux` et `python3` ; le mode projets demande Kitty 0.41.1 ou plus récent. Fish est facultatif. Sur Debian ou Ubuntu : `sudo apt install git kitty tmux python3`. Sur Fedora : `sudo dnf install git kitty tmux python3`. Kitty nécessite une session graphique ; le script d'installation n'en lance pas.

Une fois le dépôt privé créé sur GitHub et l'accès SSH configuré :

```sh
git clone git@github.com:charlestrodet/dotfiles.git ~/repos/dotfiles
cd ~/repos/dotfiles
./install.sh --dry-run
./install.sh
```

Le script crée des liens symboliques dans le dossier personnel. Il sauvegarde les fichiers déjà présents avec un suffixe `.backup-AAAAmmjj-HHMMSS`. Les modifications des fichiers suivis sont visibles après un `git pull` ; relancer le script si de nouveaux fichiers sont ajoutés ou si le dépôt change de place. `--dry-run` affiche les actions sans modifier les fichiers.

Lancer `kitty` normalement, y compris depuis un raccourci existant. La première instance encore ouverte prend le mode projets ; les instances suivantes n'activent ni tmux ni le watcher de projets. Quand l'instance de projets se ferme, la prochaine ouverture de `kitty` reprend ce rôle. Les instances simples déjà ouvertes restent simples. Kitty utilise le shell de connexion de la machine (`$SHELL`, ou celui du compte si cette variable est absente) ; le profil projets lance directement tmux sans dépendre de la configuration fish. Le choix est attaché au processus Kitty, même après un rechargement de la configuration. `Ctrl+Shift+N` crée une nouvelle instance et refait ce choix ; les nouveaux onglets (`Ctrl+Shift+T`) restent dans le profil de leur fenêtre. Ne pas utiliser `--single-instance`, qui ferait partager un même processus aux fenêtres.

## Projets et restauration

Dans l'instance de projets, un onglet Kitty sans nom explicite est temporaire. Renommer un onglet l'enregistre comme projet : son nom apparaît dans la barre Kitty et il conserve une session tmux distincte. Renommer de nouveau change son nom sans perdre la session. Fermer cet onglet, même si c'est le dernier, le retire des projets à rouvrir et termine sa session tmux. Effacer son nom le rend temporaire et retire sa sauvegarde. Fermer la fenêtre Kitty garde les projets dont les onglets étaient encore ouverts. Quand le dernier sous-onglet tmux est fermé, son onglet Kitty se ferme aussi.

À la prochaine ouverture de l'instance de projets, les projets encore enregistrés sont recréés dans leur ordre, puis rattachés à leurs sessions tmux si elles tournent toujours. Après un redémarrage complet, chaque projet retrouve ses sous-onglets, panes, dossiers, layouts, focus et le texte visible de ses panes. Un nouveau shell s'ouvre dans chaque pane ; les programmes qui tournaient ne sont pas relancés. L'historique des commandes reste géré par le shell choisi, séparément du texte visible sauvegardé.

Les informations propres à la machine — projets, chemins, ordre, captures de panes et fichier de verrou — restent dans `~/.local/state/kitty-tmux/`, avec des permissions privées. Le fichier de verrou peut rester sur le disque : seul le verrou détenu par un processus vivant compte. Rien de cet état n'est versionné dans le dépôt. `Ctrl+Shift+Alt+T` enregistre le projet immédiatement, y compris avec Kitty 0.41 (Debian Trixie). La capture des panes s'effectue environ toutes les 30 secondes quand tmux affiche sa barre, ainsi qu'en tâche de fond à la fermeture normale d'une fenêtre Kitty. Chaque capture de pane est limitée aux 2 derniers Mio de son historique ; un arrêt brutal peut perdre les toutes dernières secondes d'affichage. Si un layout sauvegardé ne peut pas être appliqué, les panes concernés sont disposés en grille et les autres sous-onglets continuent d'être restaurés. En cas de restauration incomplète, la capture d'origine reste aussi disponible dans un fichier `<id>.recovery.json` du même dossier.

## Raccourcis

Les raccourcis tmux ci-dessous concernent l'instance de projets. `Ctrl+A` est le préfixe tmux. Dans les tableaux, « puis » signifie relâcher le préfixe avant la seconde touche. `Ctrl+B` n'est plus préfixe.

### Onglets Kitty — projets

| Action | Raccourci |
| --- | --- |
| Nouvel onglet temporaire | `Ctrl+Shift+T` |
| Nouvelle instance Kitty (projets si aucune n'est ouverte) | `Ctrl+Shift+N` |
| Renommer et enregistrer l'onglet comme projet | `Ctrl+Shift+Alt+T` |
| Fermer l'onglet et retirer le projet de la réouverture | `Ctrl+Shift+Q` |
| Onglet précédent / suivant | `Ctrl+[` / `Ctrl+]` |
| Déplacer l'onglet vers la gauche / droite | `Ctrl+Shift+[` / `Ctrl+Shift+]` |

### Sous-onglets tmux — windows

| Action | Raccourci |
| --- | --- |
| Créer dans le dossier courant | `Ctrl+A`, puis `c` |
| Renommer | `Ctrl+A`, puis `,` |
| Précédent / suivant | `Alt+[` / `Alt+]` |
| Déplacer à gauche / droite et suivre le focus | `Alt+Shift+[` / `Alt+Shift+]` |
| Fermer le sous-onglet courant et tous ses panes | `Ctrl+A`, puis `&` |
| Choisir dans la liste | `Ctrl+A`, puis `w` |
| Numéro de sous-onglet | `Ctrl+A`, puis `1` à `9` |

### Panes tmux — splits et navigation

| Action | Raccourci |
| --- | --- |
| Split en dessous / à droite, avec répartition proportionnelle | `Ctrl+Shift+Entrée` / `Ctrl+Shift+|` (touche physique `\`) |
| Pane voisin | `Ctrl+Flèches` ou `Ctrl+A`, puis `h` / `j` / `k` / `l` |
| Déplacer le pane et suivre le focus | `Ctrl+Shift+Flèches` |
| Zoomer / dézoomer | `Ctrl+Shift+F` ou `Ctrl+A`, puis `z` |
| Égaliser les panes voisins | `Ctrl+A`, puis `=` |
| Redimensionner manuellement | Souris sur la bordure, `Ctrl+A`, puis `Ctrl+Flèches`, ou `Ctrl+A`, puis `r` (`R` aussi) et les flèches ; `Entrée` / `Échap` quitte le mode |
| Fermer uniquement le pane courant | `Ctrl+Shift+W` ou `Ctrl+A`, puis `x` |

Tous les splits, y compris `Ctrl+A`, puis `%` / `"`, héritent du dossier courant. Un nouveau pane prend environ `1/n` de l'espace disponible sur l'axe du split, où `n` est le nombre de panes voisins après création. Les autres gardent leurs proportions actuelles : si deux colonnes occupent 70 % et 30 % de leur groupe, une troisième donne environ 47 % / 20 % / 33 %. À la fermeture d'un pane, les voisins survivants récupèrent sa place selon leurs proportions précédentes. Ce calcul reste local au groupe concerné, même dans un layout mixte ; les arrondis et la taille minimale des panes peuvent décaler le résultat d'une cellule. `Ctrl+A`, puis `=` égalise volontairement les panes voisins.

Tmux conserve ses autres raccourcis standards non remplacés, dont `[` pour le mode copie et `d` pour détacher. `Ctrl+A`, puis `Ctrl+A` envoie un vrai `Ctrl+A` à l'application.

### Configuration

| Action | Raccourci |
| --- | --- |
| Recharger Kitty ; sourcer aussi `~/.tmux.conf` dans les projets | `Ctrl+Shift+F5` |

Les combinaisons `|`, `[` et `]` peuvent dépendre de la disposition du clavier et du serveur graphique ; les tester dans Kitty sur chaque machine. Après un changement des scripts de restauration, du shell ou du choix de profil, fermer et relancer les instances Kitty existantes.

## Contenu et limites

Le dépôt contient le thème Dracula de Kitty avec son attribution dans `current-theme.conf`. Les historiques, `fish_variables`, les clés et les sauvegardes locales restent hors du dépôt. Le gestionnaire de projets prend en charge Kitty 0.41.1 (Debian Trixie) et 0.45. Sur Kitty 0.41, renommer un projet avec `Ctrl+Shift+Alt+T` est nécessaire : les renommages effectués autrement ne déclenchent pas l'enregistrement. Sur Kitty récent, le watcher détecte aussi les autres changements de nom. La fermeture par raccourci, par clic sur l'onglet et par le bouton de la fenêtre doit être vérifiée une fois dans l'environnement graphique. L'aspect des panes restaurés peut varier si la taille du terminal change.
