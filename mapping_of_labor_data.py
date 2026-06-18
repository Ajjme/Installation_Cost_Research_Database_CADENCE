import geopandas as gpd
gdf = gpd.read_file("geo_shapefiles/") # Points to the folder containing the .shp file
print(gdf.columns)
print(gdf.head(2))