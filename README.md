# Brown GP 🏎️ | F1 Analytics Dashboard

Welcome to the **Brown GP** repository, a collection of custom Formula 1 data visualization tools. 

### 🐍 Current Project: The Timing Worm (`timing_worm.py`)
The Timing Worm is an interactive Python web application that generates a broadcast-quality, vertical "zipper-style" gap chart for any modern F1 race. Instead of relying on standard classification sheets, it pulls raw session telemetry to calculate the true physical spacing between cars on the track.

#### ✨ Key Features
* **True Gap Calculation:** Calculates exact physical time gaps based on raw finish-line telemetry rather than simplified post-race summaries.
* **Smart Lapped Car Logic:** Automatically detects backmarkers, applies median-lap penalties, and realistically spaces them behind the lead lap pack.
* **Dynamic Team Colors:** Automatically fetches and applies the official, era-accurate hex color codes for every team on the grid.
* **Interactive UI:** A streamlined web interface allowing users to select any combination of race year and track track to generate instant visualizations.

#### 🛠️ Tech Stack
* **Language:** Python
* **Web Framework:** Streamlit
* **Data Retrieval:** FastF1 / Pandas
* **Visualization:** Matplotlib

#### 🚀 How to Run Locally
If you want to run the Brown GP pit wall on your own machine:

1. Clone this repository to your local machine.
   git clone https://github.com/YOUR_USERNAME/brown-gp.git
   cd brown-gp

2. Install the required dependencies:
   pip install -r requirements.txt

3. Launch the App: Boot up the Streamlit server to view the dashboard in your browser.
   streamlit run timing_worm.py
   
Note: The first time you select a specific race, the app will pause for a minute to download and cache the official F1 telemetry data. Subsequent loads for that race will be near-instant.
