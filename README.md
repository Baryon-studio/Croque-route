# Croque-Route : mémo de mise en ligne

Objectif : l'app en ligne sur GitHub Pages (gratuit), avec la base de restaurants construite automatiquement par GitHub.

## 1. Créer le dépôt
- Sur github.com, créer un dépôt public nommé croque-route (GitHub Pages gratuit demande un dépôt public).

## 2. Envoyer les fichiers
Dans le Terminal :

    cd ~/Developer/croque-route
    git init -b main
    git add .
    git commit -m "V1"
    git remote add origin https://github.com/TON-PSEUDO/croque-route.git
    git push -u origin main

## 3. Activer l'hébergement
- Dépôt > Settings > Pages > Source : Deploy from a branch > main > / (root) > Save.
- L'app sera à l'adresse : https://TON-PSEUDO.github.io/croque-route/

## 4. Construire la base (une fois)
- Dépôt > Actions > "Construire la base de restaurants" > Run workflow.
- Compter 10 à 30 minutes. À la fin, un fichier pois.json apparaît dans le dépôt.
- La base se met ensuite à jour toute seule le 1er de chaque mois.

## 5. Sur le téléphone
- Ouvrir l'adresse de l'app, puis "Ajouter à l'écran d'accueil".

## Si le travail de construction échoue
- Ouvrir l'exécution dans Actions et copier le message d'erreur. Les serveurs publics d'OpenStreetMap refusent parfois les demandes : relancer le workflow suffit souvent.
