# Stratégie clipping : quoi reposter et quelle DA adopter

## 1. Règle n°1 : ne clippe que du contenu que tu as le droit d'utiliser

Si tu reposts des vidéos virales prises au hasard, tu prends des réclamations Content ID et des strikes. TikTok coupe la portée des comptes qui « réuploadent du contenu non original », et ces vues ne sont pas monétisables. Le modèle qui rapporte vraiment en 2026 est celui des **programmes de clipping** : un créateur ou une marque te **paie au 1 000 vues** pour clipper son contenu, avec son autorisation.

| Plateforme | Ce qu'on y trouve |
|---|---|
| **Whop Content Rewards** | Le plus gros volume. Campagnes de 0,50 à 5 $ / 1 000 vues, budget limité (premier arrivé, premier servi). |
| **Vyro** (soutenu par MrBeast) | Environ 3 $ / 1 000 vues. |
| **Clipping.net**, **Vues**, **Promote.fun** | De 0,20 à 3 $ / 1 000 vues. |

Les sources des clips sont donc **les campagnes que tu rejoins**. Tu mets les URLs des vidéos de ces créateurs dans `config.yaml`.

## 2. La niche retenue : le divertissement (YouTubeurs et streamers français)

Mesures du 10/11/2026, en vues par heure sur la dernière vidéo :

| Divertissement | | Business (pour comparer) | |
|---|---|---|---|
| Amixem | 78 000 | Legend | 5 000 |
| Inoxtag | 70 000 | Underscore_ | 5 000 |
| Joyca | 29 000 | Hasheur | 3 600 |
| Gotaga | 24 000 | Finary | 2 000 |
| Mastu | 24 000 | Yann Darwin | 80 |

Sur Twitch, le meilleur clip de la semaine : Domingo 13 500 vues, Squeezie 6 800, Kameto 3 100, Amine 3 000.

**Ce qu'on y gagne :** une audience 10 à 15 fois plus large, des vidéos de groupe (« ft. ») pleines de moments forts, et Twitch qui sort du contenu frais tous les jours.

**Ce qu'il faut savoir :**
- Le CPM est plus bas (de 1 à 4 $), donc il faut beaucoup de vues.
- La concurrence est forte : beaucoup de chaînes de fans clippent déjà ces créateurs.
- Les gros YouTubeurs ont Content ID et peuvent réclamer les revenus d'un clip ou le bloquer.
- Il faut privilégier les créateurs qui tolèrent ou paient le clipping (campagnes Whop « streamers/gaming »).

## 3. Ce qu'il faut découper : les moments qui font des vues

L'IA du pipeline note chaque moment selon ces critères (prompt dans `clipper/highlights.py`) :

| Type de moment | Exemple |
|---|---|
| **Fou rire / réaction extrême** | quelqu'un qui craque, hurle, pleure de rire |
| **Moment WTF** | ce qui se passe est complètement inattendu |
| **Rage / clash** | une embrouille entre invités, un streamer qui pète un câble |
| **Anecdote incroyable** | « La fois où j'ai… » |
| **Retournement** | ça tourne mal, ou d'un coup très bien |
| **Phrase culte** | une réplique qui va devenir un mème |

**Ce qu'il faut éviter :** les intros, le contexte trop long, les blagues internes, les moments qui ne se comprennent pas sans la vidéo complète.

**Durée :** de 25 à 60 s. La phrase d'accroche doit tomber **dans la première seconde**. Pas d'intro, on coupe directement dans l'action.

## 4. La DA (direction artistique) codée dans le pipeline

```
┌──────────────────────┐
│  HOOK EN HAUT (3 s)  │  ← texte blanc sur bandeau noir, 5 à 8 mots max
│                      │
│   visage recadré     │  ← crop 9:16 centré sur le visage (sinon fond flou)
│      plein écran     │
│                      │
│  SOUS-TITRES 2-4 MOTS│  ← blanc, gros contour noir, mot prononcé en jaune
│                      │  ← zone sûre : au-dessus des boutons TikTok/IG
└──────────────────────┘
```

- **Sous-titres lisibles, pas « arc-en-ciel ».** Les tests 2026 montrent que les sous-titres trop animés font baisser la rétention. Le pipeline utilise un style sobre : blanc, contour épais, mot actif en jaune, 2 à 4 mots à la fois, police très grasse.
- **Hook écrit à l'écran pendant 3 s**, généré par l'IA à partir du moment le plus fort. Il reformule, il ne paraphrase pas.
- **Pas de silences.** Le clip démarre sur une phrase, jamais sur un blanc.
- **Rendu « brut mais propre ».** Les montages trop léchés façon pub sont zappés en 2026. Pas de transitions, pas d'effets sonores inutiles.
- **Description courte et hashtags.** 1 phrase + 3 à 5 hashtags de niche. Les hashtags génériques comme #fyp ne servent à rien.

## 5. Rythme de publication : rester sous le radar anti-spam

YouTube ne publie aucun chiffre officiel. Ce qu'il sanctionne, c'est **le contenu répétitif ou repris sans rien ajouter**. Depuis la mise à jour Shorts d'octobre 2026, les rediffusions brutes perdent de la portée et les « fermes à clips » sont visées.

L'agent applique trois protections :

1. **Montée en charge progressive.** Semaine 1 : 3 Shorts par jour. Puis 5, 8, 12, et un **plafond de 15 par jour**. Les créneaux sont répartis entre 9h00 et 21h30. On ne monte au-delà que si les vues ne baissent pas.
2. **Valeur ajoutée dans chaque Short.** Un hook écrit, des sous-titres, et la carte « LE VERDICT » à la fin avec un commentaire de Clip by Cut. La description crédite la source.
3. **Gabarits qui changent.** Trois variantes de couleurs tournent d'un clip à l'autre, pour éviter un modèle identique sur toutes les vidéos.

Pense aussi à vérifier ton numéro de téléphone dans YouTube Studio : sans ça, YouTube limite les envois quotidiens.

## 6. Les limites techniques à connaître

- **TikTok :** une appli non auditée ne peut publier qu'en **privé**. C'est pour ça que la publication passe par les outils de programmation gratuits des plateformes (voir README).
- **Instagram :** il faut un compte Pro (Créateur ou Business) relié à une page Facebook.
- **YouTube :** une vidéo verticale de moins de 60 s devient automatiquement un Short.
