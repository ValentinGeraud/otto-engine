# Clip by Cut : agent de clipping automatique (100 % gratuit)

L'agent **trouve les vidéos du moment** (YouTube + Twitch), **découpe les meilleurs passages** avec une IA locale gratuite, **monte des clips 9:16 sous-titrés** et **prépare la publication** sur TikTok, Instagram Reels et YouTube Shorts.

Avant de commencer, lis la stratégie et la DA dans [STRATEGIE.md](STRATEGIE.md). La photo de profil est [branding/photo_profil.png](branding/photo_profil.png).

```
découverte (vues/heure) → téléchargement → transcription (Whisper, local)
  → IA locale Ollama : meilleurs moments + hook + description
  → montage 9:16 (crop visage, sous-titres mot à mot, hook) → a_poster/ (+ YouTube auto en option)
```

## Ce qui coûte 0 €

| Brique | Outil gratuit |
|---|---|
| Téléchargement | yt-dlp |
| Transcription | faster-whisper, sur ton PC |
| Choix des moments et hooks | **Ollama + qwen3:8b**, sur ton PC (déjà installé chez toi) |
| Montage | ffmpeg |
| Publication TikTok | **TikTok Studio** (programmation jusqu'à 10 jours à l'avance, compte Créateur ou Business) |
| Publication Instagram | **Meta Business Suite** (programmation des Reels) |
| Publication YouTube | **YouTube Studio**, ou envoi automatique avec l'API officielle gratuite |

Pourquoi pas une publication 100 % automatique sur TikTok et Instagram ? Les API officielles sont gratuites, mais TikTok bloque en privé les vidéos postées par une appli non validée. Les outils qui contournent ça (Postiz hébergé, Buffer...) sont payants. Les « robots » qui pilotent le navigateur font bannir les comptes. Programmer 1 fois par semaine prend environ 15 minutes et ne présente aucun risque.

## Installation (une seule fois)

```bash
pip install -r requirements.txt
```

Ollama tourne déjà sur ton PC avec `qwen3:8b`. Sur un autre PC : installe ollama.com puis lance `ollama pull qwen3:8b`.

## Utilisation

```bash
python agent.py
```
Découvre, découpe, puis remplit `a_poster/` avec un dossier par créneau. Chaque dossier contient `video.mp4` et `A_COLLER.txt` (description TikTok/Insta, titre YouTube, date et heure).

```bash
python agent.py --url "https://www.youtube.com/watch?v=..."
```
Traite une vidéo précise.

```bash
python agent.py --no-publish
```
Génère seulement les clips dans `output/`, pour tester le rendu.

**Ta routine de la semaine (environ 15 min) :** ouvre `a_poster/`. Pour chaque dossier, programme `video.mp4` à la date indiquée dans TikTok Studio, Meta Business Suite et YouTube Studio, en collant les textes de `A_COLLER.txt`. Supprime ensuite le dossier.

## Tableau de bord (suivi en direct)

```bash
python dashboard.py
```
Ouvre ensuite http://localhost:8787. Tu y vois :
- la vidéo en cours et son étape (téléchargement → transcription → sélection IA → montage → publication) ;
- les vidéos du moment repérées ;
- chaque clip avec son aperçu (clic pour le lire), son hook, son verdict, son statut (monté / en file / programmé) et son lien YouTube ;
- les posts du jour, le palier anti-spam et le prochain créneau.

Pour que le tableau de bord suive un passage, lance l'agent avec `python agent.py > run.log 2>&1`.

## Option : YouTube Shorts 100 % automatique (gratuit)

1. Va sur console.cloud.google.com, crée un projet et active **YouTube Data API v3**.
2. Configure l'écran de consentement OAuth (type « Externe ») et ajoute ton adresse Gmail comme utilisateur test.
3. Crée un identifiant **OAuth « Application de bureau »**, télécharge le JSON et enregistre-le sous `secrets/client_secret.json`.
4. Dans `config.yaml`, mets `publish.backend: youtube_api`. Au premier lancement, une fenêtre Google s'ouvre pour autoriser l'accès.
5. ⚠️ Tant que le projet n'est pas validé par Google, les vidéos envoyées par l'API restent **privées**. Pour lever ce blocage, remplis le formulaire gratuit « YouTube API Services – Audit and Quota Extension ». Le quota est d'environ 6 envois par jour.

## Objectif 50 Shorts par jour

- L'agent fabrique environ 60 clips par passage (12 vidéos sources × 5 clips) et les met en ligne aux créneaux répartis entre 7h00 et 23h30.
- **Quota gratuit de l'API YouTube : 6 envois par jour.** Les clips en trop restent dans une **file d'attente** et partent automatiquement les jours suivants. L'agent arrête d'en produire au-delà de 150 clips en attente.
- **Pour monter à 50 par jour, c'est gratuit :** remplis le formulaire Google « YouTube API Services – Audit and Quota Extension » (projet `clip-by-cut`, environ 85 000 unités/jour nécessaires). L'audit lève aussi le blocage « vidéos privées ». Une fois accepté, mets `api_uploads_per_day: 50` dans `config.yaml`.
- ⚠️ **Démarre doucement.** Une chaîne neuve qui publie 50 vidéos par jour de contenus repris est vite signalée comme spam par YouTube (contenu répétitif ou réutilisé), ce qui peut aller jusqu'à la suppression de la chaîne. Monte progressivement : 6 par jour la première semaine, puis 15, puis 30. Et vérifie ton numéro de téléphone dans YouTube Studio, sinon YouTube limite le nombre d'envois quotidiens.

## Chaînes de test (autres niches)

Le même pipeline alimente plusieurs chaînes. Chaque chaîne secondaire a un dossier `channels/<id>/` avec un `config.yaml` qui ne contient que ce qui change (sources, langue, niche, DA, horaires). Son `state.json`, ses clips (`output/`) et son `run.log` restent dans ce dossier.

| id | Langue | Niche | Sources |
|---|---|---|---|
| `en_business` | anglais | interviews d'entrepreneurs, histoires de fondateurs, actu finance et déclarations | Diary of a CEO, PBD, Hormozi, Starter Story, My First Million, Graham Stephan, Meet Kevin, CNBC |
| `es_historias` | espagnol | faits divers, histoires vraies incroyables, mystères | The Wild Project, Dross, El Rincón de Giorgio, Relatos de la Noche, Mundo Desconocido, Curiosidades con Mike |

```bash
python agent.py --channel en_business
python agent.py --channel es_historias
```

**Pour activer la publication d'une chaîne :**
1. Crée la chaîne YouTube (compte de marque sur le même Gmail, c'est plus simple), puis vérifie son numéro de téléphone.
2. Dans `channels/<id>/config.yaml`, remplace `youtube_channel: "REMPLACE_MOI"` par le nom exact de la chaîne. Tant que ce n'est pas fait, l'agent monte les clips sans rien publier.
3. Lance `python youtube_auth.py --channel <id>` et choisis cette chaîne dans la fenêtre Google. Le jeton est enregistré dans `secrets/youtube_token_<id>.json`.

**Quota : un projet Google Cloud par chaîne.** Le quota de l'API YouTube (environ 6 envois/jour) est compté par projet. Chaque chaîne de test a donc son propre projet : refais les étapes 1 à 3 de la section « YouTube Shorts 100 % automatique » ci-dessous dans un nouveau projet, et enregistre l'identifiant OAuth sous `secrets/client_secret_en.json` ou `secrets/client_secret_es.json`. L'agent tient les compteurs par projet dans `quota.json`.

Le passage quotidien (`autostart/run_agent.cmd`) traite les 3 chaînes l'une après l'autre. Le tableau de bord et le QG suivent uniquement la chaîne principale. Pour les stats d'une chaîne de test : `python analytics.py --channel <id>`.

## Lancer l'agent tout seul chaque jour (Windows)

```bash
schtasks /create /tn "ClipByCut" /sc daily /st 08:00 /tr "python \"C:\chemin\vers\clipper\agent.py\""
```

## Réglages utiles (`config.yaml`)

| Réglage | Effet |
|---|---|
| `watch:` | Les chaînes YouTube et Twitch à surveiller. |
| `ai` | `ollama` (gratuit, local), `claude` (payant, meilleurs hooks) ou `heuristic` (sans IA). |
| `focus` | Le type de moments à privilégier. Ajuste-le selon tes stats. |
| `style.*` | La DA : police, tailles, couleurs, position des sous-titres, durée du hook. |
| `publish.slots` | Les heures de publication. |
| `whisper_model` | `medium` = transcription plus précise mais plus lente. |
