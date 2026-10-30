# Sito della Rete UdR 7

Sito con mappa interattiva e quadri orari della rete, generato dal GTFS. È un unico file HTML autonomo
(la libreria delle mappe Leaflet è inclusa): si apre con un doppio clic o si pubblica così com'è.

## Funzioni

- **Elenco linee per comune** (dal prefisso del codice linea: AMA Amatrice, CAN Cantalice, CITT Cittaducale,
  CON Contigliano, CSA Castel Sant'Angelo, LEO Leonessa; le linee con codice numerico, es. 29_1 o 66_3, sono
  le intercomunali) con ricerca per linea, comune o fermata e numero di corse nel giorno scelto.
- **Giorno**: si sceglie una data; linee, corse e partenze seguono i calendari.
- **Calendari**: non si usano `calendar.txt` e `calendar_dates.txt` del GTFS (che coprono solo due settimane),
  ma quelli impostati in `CALENDARI` in testa a `build_data.py`, senza eccezioni:
  - `udr7_10` *Scolastico feriale*: dal lunedì al venerdì, dal 21/09/2026 all'08/06/2027;
  - `udr7_20` *Scolastico sabato*: il sabato, dal 21/09/2026 all'08/06/2027;
  - la domenica non ci sono corse.
- **Mappa** (sfondi Esri: stradale, topografica, grigio chiaro, satellite): percorsi della linea con frecce del
  senso di marcia, capolinea P/A, varianti di percorso evidenziabili, filtro per zone, schermo intero, stampa.
- **Quadro orario** per calendario e direzione, con fermate principali o tutte; clic su una corsa per vederla
  sulla mappa, clic su una fermata per le sue partenze del giorno.
- **Vicino a me** e **Bus in viaggio** (posizioni stimate dagli orari programmati, non in tempo reale).
- Orari sempre nel formato **hh:mm**: gli orari del GTFS con i secondi (es. 06:54:30) sono troncati al minuto.

Il GTFS non indica le fermate principali (`timepoint` è sempre 1): sono considerate principali i capolinea,
la prima fermata in ogni comune, i nodi serviti da almeno 4 altre linee e una fermata almeno ogni 5 minuti
di viaggio (parametri in testa a `build_data.py`).

I nomi delle fermate sono ripuliti (il comune passa dal prefisso `COMUNE /` a una colonna a parte, con la grafia
estesa: `TORRI` → Torri in Sabina, `CASTELNUOVO F.` → Castelnuovo di Farfa); le fermate senza comune nel nome
prendono quello della fermata con comune più vicina. Le direzioni seguono `direction_id`; il titolo di ogni
direzione è dato dai capolinea d'arrivo delle corse, perché i `trip_headsign` del GTFS sono spesso imprecisi.

## File

- `template.html`: grafica e funzioni del sito, con i segnaposto per i dati.
- `logo.png`: logo Astral Infomobilità al centro della testata (incluso nella pagina; per cambiarlo basta
  sostituire il file).
- `build_data.py`: legge il GTFS e scrive il sito completo in `sito-mappe/index.html` (pubblicato online) e
  `index.html` nella radice del repo.

Online (GitHub Pages) viene pubblicato **solo** `index.html`: script, modello, README e file GTFS restano nel
repository e non sono raggiungibili dall'indirizzo del sito. La pagina non mostra il nome del file GTFS.

Le visite sono contate con **GoatCounter** (account `andreaantonini`, https://andreaantonini.goatcounter.com):
pagina e linee aperte (`#linea-…`), senza cookie. Lo script è in fondo a `template.html`.

## Aggiornare con un nuovo GTFS

**Da GitHub**: *Add file → Upload files*, trascina il nuovo zip GTFS e fai *Commit changes* su `main`.
Il workflow *Pubblica sito mappe* rigenera e ripubblica il sito in un paio di minuti (scheda *Actions*).
Il sito usa lo zip più recente presente nella radice del repo.

**Dal computer** (serve solo Python 3):

```bash
python3 sito-mappe/build_data.py [percorso/GTFS.zip]
```
