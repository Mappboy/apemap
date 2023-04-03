# This script fetches abs resources and unzips them

if [ ! -f "ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip" ]; then
    echo "Downloading ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip"
    curl "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip" -o "ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip"
    unzip ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip
    rm ASGS_2021_MAIN_STRUCTURE_GPKG_GDA2020.zip
fi

if [ ! -f "ASGS_Ed3_2021_RA_GPKG_GDA2020.zip" ]; then
    echo "Downloading ASGS_Ed3_2021_RA_GPKG_GDA2020.zip"
    curl "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/ASGS_Ed3_2021_RA_GPKG_GDA2020.zip" -o "ASGS_Ed3_2021_RA_GPKG_GDA2020.zip"
    unzip ASGS_Ed3_2021_RA_GPKG_GDA2020.zip
    rm ASGS_Ed3_2021_RA_GPKG_GDA2020.zip
fi

if [ ! -f "ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip" ]; then
    echo "Downloading ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip"
    curl "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip" -o "ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip"
    unzip ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip
    rm ASGS_2021_SUA_UCL_SOS_SOSR_GPKG_GDA2020.zip
fi

if [ ! -f "ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip" ]; then
    echo "Downloading ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip"
    curl "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/access-and-downloads/digital-boundary-files/ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip" -o "ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip"
    unzip ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip
    rm ASGS_Ed3_Non_ABS_Structures_GDA2020_GPKG_updated_2022.zip
fi

if [ ! -f "2021-Cwlth_electoral_boundaries_TAB.zip" ]; then
    echo "Downloading 2021-Cwlth_electoral_boundaries_TAB.zip"
    curl "https://www.aec.gov.au/Electorates/gis/files/2021-Cwlth_electoral_boundaries_TAB.zip" -o "2021-Cwlth_electoral_boundaries_TAB.zip"
    unzip 2021-Cwlth_electoral_boundaries_TAB.zip
    rm 2021-Cwlth_electoral_boundaries_TAB.zip
    ogr2ogr -f "GPKG" 2021_ELB.gpkg 2021_ELB.TAB
fi
# Get rid of unneeded metadata
rm *GDA2020.xml
