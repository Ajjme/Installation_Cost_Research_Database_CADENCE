# Installation_Cost_Research_Database_CADENCE
This will connect to various government APIs to to pull labor costs and building component costs needed for our BCR analysis in the CADENCE Project. We will later connect with Dr. Fan to get the actual cost based on permitting data.


Research:
Publicly Available & Government Databases
U.S. Bureau of Labor Statistics (BLS) – Occupational Employment and Wage Statistics (OEWS)
What it offers: A massive, free public database tracking employment and wage data across specific geographic regions.

Key metrics to pull: You can track wage percentiles using Standard Occupational Classification (SOC) codes. For your model, look at SOC 47-2181 (Roofers) and SOC 47-1011 (First-Line Supervisors of Construction Trades).

Limitation: This provides excellent macroeconomic data localized by Metropolitan Statistical Area (MSA), but it reflects raw wages and benefits rather than crew productivity metrics or GC business markups.

Things to keep in mind. Difference between bare labor costs and burdened labor costs. incorporate a regional markup multiplier to scale raw hourly wages up to true asset-installation costs—especially since roofing carries significantly higher insurance burdens than other trades.

Data:
SOC 47-2181 (Roofers): For your baseline roofing trade labor cost.

SOC 47-2061 (Construction Laborers): Often a large part of the roofing crew mix.

SOC 47-1011 (First-Line Supervisors of Construction Trades): Useful for indexing General Contractor/supervisory trade costs.

H_MEAN: Hourly mean wage (the absolute baseline cost per hour).

H_PCT10 to H_PCT90: Wage percentiles. These are incredibly useful for your vulnerability modeling if you want to run sensitivity analyses or Monte Carlo simulations on varying economic conditions (e.g., using the 75th percentile to simulate a post-disaster demand surge where labor costs spike). (this will be a future add in the economic analysis)

Mapping_app.py

Task 2 Calculation of Labor use per roofing process
we will need to calculate how many hours of work go into replacing a roof and by who
separately we will need to calculate how many hours of work go into reparing a roof at a certain percent damage

We will need to have some fixed cost determination for having people come out and examine the roof
Ask for a database on roofing claims
We will also need to determine what the deductable mark should be (I think this can be a User input that changes)
if damage is below the deductable then we dont add that damage to the projected capital allocation metric (or should we?)

Task 3 calculate the per squarefootage cost of each selected material and see if there is any geographical changes to this cost

The output from this analysis will be a database of by roofing type the cost per squarefoot the cost in labor at the most granular level MFA 
Columns Roof Asset, Location ("Shapefile Property Name for Area Code:", "msa7"), Roofer Labor cost per hour, Construction Laborers cost per hour, claims adjusters, examiners and investigators cost per hour (H_Mean) Total labor cost per hour (research how many people working at a time (many be do three levels small roof medium roof large roof)) 

Other database will have size of roof and that we will tie to the per hour rate - Define number of hours to complete the installation of a large roof small roof and medium roof
Labor rates would not change by asset type but hours needed per size of the roof would change

Also need to determine the repair times (this will be based on the percent damage from the vulnerability curves)
the percent damage will also be tied to the size of the roof and the percent will relate to the number of hours


that database will then be referenced in the main system
we will produce intermidate outputs which are the interactive graphics of the nation in Stream lit that allows us to show the prices over time
Andrew Johnson To Dos.
1. Ask insurance group for access to Verisk
2. fill gaps in map with national average
3. center map in streamlit
4. get a CIRCAD CADENCE Streamlit style that we can apply to all of our graphics
    a. drop in logos
    b. make cool cadence logo?
5. research other color_continuous_scale="Viridis" styles
6. contact Dr. Fan to check on progress 
Reach out to UGA team to gett official definisition f the roofing tyopes we are using
7. see if there are any good per sq databases about roofing material as that will inform what the UGA group should build - Arcadious construction data base reach out
8. ask the IAB if we should do it by sq or just the three levels (I am thinking sq sense we are already collecting that data, but we will do the number of workers by size of sq in bins)

Sources: https://www.bls.gov/oes/tables.htm


Methodology reasoning
Why Gaps Exist for our Target Occupations
The BLS suppresses data at the local level for two main reasons:

The "Fewer Than Three" Rule (Confidentiality): If a rural BOS region or a small MSA only has one or two major employers employing a specific trade, the BLS hides the wage metrics so you cannot reverse-engineer what a specific local company pays its staff.

Sample Size Limits (Statistical Reliability): If a remote region doesn't return enough completed employer surveys for a highly niche or highly executive role, the BLS suppresses the data because the sample size is too small to calculate a statistically sound average.