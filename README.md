# Binance Tax FR : Calcul plus/moins-values crypto 2025

Script local Python qui calcule tes plus/moins-values crypto pour ta déclaration fiscale française 2025 (formulaire 2086) à partir de l'API Binance, selon la méthode officielle de l'article 150 VH bis du CGI.

> ⚠️ **Avertissement** : Ce rapport n'est **PAS un document fiscal officiel** ni opposable à l'administration fiscale. Il s'agit d'une aide au calcul. Pour un rapport certifié et opposable, utilise des services payants comme **Waltio** ou **Koinly**. Vérifie systématiquement les chiffres avant de les reporter dans ta déclaration.

---

## Prérequis

- macOS avec Python 3.9 ou plus récent (`python3 --version` pour vérifier)
- Un compte Binance avec accès à l'historique complet 2022–2025
- Aucun autre exchange ou wallet (sinon le calcul sera incomplet)

## Étape 1 : Créer une clé API Binance (lecture seule)

1. Connecte-toi sur [binance.com](https://www.binance.com)
2. Menu profil (en haut à droite) → **API Management**
3. Clique **Create API** → choisir **System generated**
4. Donne un label (ex : `tax-fr-readonly`) et valide avec ton 2FA
5. Une fois créée, clique **Edit restrictions** :
   - ✅ Coche UNIQUEMENT **Enable Reading**
   - ❌ Décoche tout le reste (Enable Spot Trading, Enable Withdrawals, etc.)
   - ✅ Restriction IP : recommandé (ajoute ton IP fixe si possible)
6. Sauvegarde ta `API Key` et ton `Secret Key` : le secret n'est affiché qu'une seule fois !

## Étape 2 : Configurer les clés

1. Dans le dossier du projet, copie `.env.example` vers `.env` :
   ```bash
   cp .env.example .env
   ```
2. Ouvre `.env` dans un éditeur de texte
3. Remplace `ta_cle_ici` et `ton_secret_ici` par tes vraies clés Binance
4. **Ne partage JAMAIS ce fichier `.env`** : il contient tes secrets

## Étape 3 : Installation

Double-clique sur **`install.command`** dans le Finder.

(Si macOS bloque, fais clic droit → Ouvrir → confirme. Ou en terminal : `chmod +x install.command run.command`.)

L'installation crée un environnement Python virtuel (`venv/`) et installe les dépendances.

## Étape 4 : Lancer le rapport

Double-clique sur **`run.command`**.

Le script :
1. Récupère tout l'historique Binance depuis 31/01/2022
2. Récupère les prix EUR via CoinGecko (gratuit, peut prendre 5-15 min la 1ʳᵉ fois)
3. Applique la formule officielle française pour chaque cession SEPA EUR de 2025
4. Génère `rapport_fiscal_2025.html` et l'ouvre automatiquement

## Que faire avec le rapport ?

Le rapport HTML contient :
- Le détail des 6 cessions SEPA 2025 → à reporter dans le **formulaire 2086**
- Le total plus-values → case **3AN** du formulaire **2042 C**
- Le total moins-values → case **3BN** (reportables 10 ans)
- Liste des actifs au 31/12/2025

**Ne pas oublier** :
- ✅ Cocher la case **8UU** sur le formulaire 2042 (compte d'actifs numériques à l'étranger)
- ✅ Remplir un formulaire **3916-bis** par compte étranger (= 1 pour Binance)

Pour imprimer en PDF : ouvre le fichier HTML dans Safari/Chrome → `⌘P` → Enregistrer en PDF.

---

## Sécurité

- Les clés API restent en local, dans `.env` (jamais envoyées ailleurs)
- L'API Binance est appelée en **lecture seule**
- CoinGecko est interrogé sans clé API (juste les prix publics)
- Aucun envoi de données vers un serveur tiers

## Dépannage

- **"Invalid API-key"** : vérifie le contenu de `.env`, et que la clé est bien activée côté Binance
- **Rate limit CoinGecko** : le script respecte un délai entre appels ; relance si interrompu (cache local utilisé)
- **Aucune cession trouvée** : vérifie que tu as bien fait des retraits SEPA en 2025 sur Binance

## Limites

- Couvre uniquement Binance. Si tu as d'autres wallets, le calcul est faux.
- Ne gère pas les airdrops, NFT, futures, marges, etc.
- Si CoinGecko ne connaît pas un token, il est ignoré (avertissement affiché).

Pour un rapport officiel et exhaustif, **recommandation** : [Waltio](https://www.waltio.com) ou [Koinly](https://koinly.io).
