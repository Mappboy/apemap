""" APE Map Dashboard inspration
https://github.com/plotly/dash-sample-apps/blob/main/apps/dash-medical-provider-charges/app.py
https://github.com/plotly/dash-sample-apps/blob/main/apps/dash-oil-and-gas/app.py
https://github.com/plotly/dash-sample-apps/blob/main/apps/dash-spatial-clustering/app.py

TODO - Add age range slider and histogram
- Create text box for select school funding enrolment and students
- Add select theme
"""
import math
import os
import pathlib
import random
from datetime import date

import dash_bootstrap_components as dbc
import geopandas as gpd
import pandas as pd
import plotly.express as px
from dash import Dash, html, dcc, dash_table, Input, Output, State
from dash.exceptions import PreventUpdate
from dotenv import load_dotenv
from shapely import Polygon, Point
from sqlalchemy import create_engine

BGCOLOR = "#222"

TRANSPARENT = "rgba(0,0,0,0)"

load_dotenv("../.env")

root_data_dir = pathlib.Path("..").resolve() / "data"
ext_data_dir = root_data_dir / "external"
geopackage = root_data_dir / "aped.gpkg"

USE_POSTGRES = False

if USE_POSTGRES:
    engine = create_engine(
        f"postgresql+psycopg://{os.environ.get('DATABASE_USERNAME')}:{os.environ.get('DATABASE_PASSWORD')}@localhost:5432/{os.environ.get('DATABASE_NAME')}"
    )
else:
    import sqlite3

    engine = sqlite3.connect(geopackage, check_same_thread=False)


def geopackage_col_to_list(df, col, convert_ints=False):
    if not isinstance(df[col][0], list):
        parliamentarians[col] = (
            parliamentarians[col]
            .str.replace(r"\d+:", "", regex=True)
            .str.replace(r"\(|\)", "", regex=True)
            .str.split(",")
        )
        # convert to integers and remove empty strings or non integers
        if convert_ints:
            parliamentarians[col] = parliamentarians[col].apply(
                lambda x: [int(i) for i in x if i.isdigit()]
            )
    return df[col]


today = date.today()

parliamentarians = pd.read_sql(
    """
SELECT member_aph.* FROM member_aph
    ORDER BY "GivenName" asc
""",
    engine,
)
parliamentarians.drop(["Age"], axis=1, inplace=True)
parliamentarians.drop_duplicates(["mp_id"], inplace=True)
# fix up if using geopacakage and not array
# if parliamentarians["RepresentedParliaments"] not an array then str remove first integer and convert to array
if not isinstance(parliamentarians["RepresentedParliaments"], list):
    parliamentarians["RepresentedParliaments"] = geopackage_col_to_list(
        parliamentarians, "RepresentedParliaments"
    )

parliamentarians = parliamentarians.explode("RepresentedParliaments")
parliamentarians["age"] = pd.to_datetime(parliamentarians["dob"]).apply(
    lambda x: today.year - x.year - ((today.month, today.day) < (x.month, x.day))
)
parliamentarians["age_group"] = pd.cut(
    parliamentarians["age"],
    bins=[18, 21, 30, 40, 50, 60, 70, 80, 90, 159],
    labels=[
        "18-20",
        "21-30",
        "31-40",
        "41-50",
        "51-60",
        "61-70",
        "71-80",
        "81-90",
        "91+",
    ],
    ordered=False,
)
# parliamentarians = parliamentarians[
#     ["member", "chamber", "age", "dob", "district", "party_abbrev", "RepresentedParliaments", "Gender",
#      "State", "Image", "wiki_link"]]
parliamentarians.columns = parliamentarians.columns.str.lower().str.replace(" ", "_")
STATE_LOOKUP = {
    i["state"]: i["stateabbrev"]
    for i in parliamentarians[["state", "stateabbrev"]]
    .drop_duplicates()
    .to_dict("records")
}


def create_gdf():
    if USE_POSTGRES:
        gdf = gpd.read_postgis(
            'SELECT m.*, "total enrolments" as total_students '
            "FROM member_secondary_school_education m "
            'LEFT JOIN acara_school_profile_2022 a on m.acara_id = a."acara sml id"::int ORDER BY member asc, "school name"',
            engine,
            geom_col="geom",
        )
    else:
        gdf_47 = gpd.read_file(geopackage, layer="member_secondary_school_education")
        gdf_47 = gdf_47.convert_dtypes()
        asp_47 = gpd.read_file(geopackage, layer="acara_school_profile_2022")[
            ["acara sml id", "total enrolments"]
        ]
        asp_47["acara sml id"] = asp_47["acara sml id"].astype(int)
        gdf = pd.merge(
            gdf_47, asp_47, left_on="acara_id", right_on="acara sml id", how="left"
        )
        gdf.rename(columns={"total enrolments": "total_students"}, inplace=True)
    gdf = gdf.convert_dtypes()
    # fill missing students with min 50 students
    gdf["total_students"] = (
        gdf["total_students"].fillna("50").apply(lambda x: "50" if not x else x)
    )
    gdf["total_students"] = gdf["total_students"].astype(int)
    gdf.columns = gdf.columns.str.lower().str.replace(" ", "_")
    # add small random number to lat/lon to avoid overlapping points
    gdf["lat"] = gdf.geometry.y.apply(lambda x: x + random.uniform(-0.0001, 0.0001))
    gdf["lon"] = gdf.geometry.x.apply(lambda x: x + random.uniform(-0.0001, 0.0001))
    gdf["geometry"] = gdf.apply(lambda x: Point(x["lon"], x["lat"]), axis=1)
    return gdf


gdf = create_gdf()


def get_chamber_counts(df):
    return df.groupby(["chamber"]).size().reset_index(name="count")


def get_party_counts(df):
    return df.groupby(["party_abbrv"]).size().reset_index(name="count")


def get_gender_counts(df):
    return df.groupby(["gender"]).size().reset_index(name="count")


# implement map
# https://dash.plotly.com/holoviews + datashader + Rapids
# Scattergeo
layout = dict(
    height=800,
    margin={"r": 0, "t": 0, "l": 0, "b": 0},
    legend=dict(
        bgcolor=BGCOLOR,
        orientation="h",
        yanchor="bottom",
        y=1,
        xanchor="center",
        x=1,
        font=dict(family="Roboto", size=12, color="lightgrey"),
    ),
    hovermode="closest",
    title="Schools Overview",
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
    mapbox=dict(
        style="dark",
        center=dict(lat=-25.2744, lon=133.7751),
        zoom=3,
    ),
)

app = Dash(
    title="APE 🦍 Map ",
    external_stylesheets=[dbc.themes.DARKLY],
    meta_tags=[
        {
            "name": "viewport",
            "content": "width=device-width, initial-scale=1, maximum-scale=1.0, user-scalable=no",
        }
    ],
)

# create a plotly express scatter map coloured by school sector with marker size based on the number of students
hovertemplate = "<b>name</b>: %{name}<br>" "<b>customdata</b>: %{customdata}<br>"
map_cols = [
    "mp_id",
    "member",
    "name",
    "school_sector",
    "party",
    "party_abbrv",
    "chamber",
    "district",
    "total_students",
    "gender",
    "stateabbrev",
]

table_cols = [
    *map_cols,
    "australian_government_recurrent_funding_per_student",
    "australian_government_recurrent_funding_total",
    "state__territory_government_recurring_funding_per_student",
    "state__territory_government_recurring_funding_total",
    "fees_charges_and_parent_contributions_per_student",
    "fees_charges_and_parent_contributions_total",
    "other_private_sources_per_student",
    "other_private_sources_total",
    "total_gross_income_per_student",
    "total_gross_income_total",
]
gdf = gdf[[*map_cols, "geometry"]]
px.set_mapbox_access_token(os.environ.get("MAPBOX_TOKEN"))
fig = px.scatter_mapbox(
    gdf,
    lat=gdf.geometry.y,
    lon=gdf.geometry.x,
    color="school_sector",
    size="total_students",
    color_discrete_sequence=px.colors.qualitative.Dark24,
    mapbox_style="dark",
    hover_data=map_cols[1:],
    zoom=4,
    height=800,
)
fig.update_layout(**layout)
fig.update_traces(cluster=dict(enabled=True, maxzoom=11))
# set hover_template to show all values in the cluster

# Todo add table callback to zoom to location
table = dash_table.DataTable(
    id="table",
    data=gdf[map_cols].to_dict("records"),
    page_size=25,
    columns=[
        {"name": i, "id": i, "deletable": True, "selectable": True} for i in gdf.columns
    ],
    style_header={"backgroundColor": "rgb(30, 30, 30)", "color": "white"},
    style_data={"backgroundColor": "rgb(50, 50, 50)", "color": "white"},
)

# Pie_figs
pie_fig = px.pie(
    gdf,
    values="total_students",
    names="school_sector",
    title="school_sector",
    color_discrete_sequence=px.colors.qualitative.Dark24,
)
pie_fig.update_layout(
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
)

# gender of parliamentatarians
pie_chamber_fig = px.pie(
    gdf,
    values="total_students",
    names="school_sector",
    title="school_sector",
    color_discrete_sequence=px.colors.qualitative.Dark24,
)
pie_chamber_fig.update_layout(
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
)

pie_gender_fig = px.pie(
    get_gender_counts(gdf),
    values="count",
    names="gender",
    title="Gender",
    color_discrete_sequence=px.colors.qualitative.Dark24,
)
pie_gender_fig.update_layout(
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
)

pie_party_fig = px.pie(
    get_party_counts(gdf),
    values="count",
    names="party_abbrv",
    title="Party",
    color_discrete_sequence=px.colors.qualitative.Dark24,
)
pie_party_fig.update_layout(
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
)

# create histogram of parliamentarians by age and gender
# add two traces one from males and females


histogram = px.bar(
    parliamentarians.groupby(["party_abbrev", "gender"])["age"]
    .agg({"mean"})
    .reset_index()
    .rename({"mean": "avg_age"}, axis=1),
    x="party_abbrev",
    y="avg_age",
    color="gender",
    barmode="group",
)
histogram.update_layout(
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
)

app.layout = html.Div(
    [
        html.H1(children="APE 🦍 Map", style={"textAlign": "center"}),
        dcc.Geolocation(id="geolocation"),
        dcc.Store(id="viewport-store"),
        dcc.Store(id="bounds-store"),
        dbc.Row(
            [
                dbc.Col(
                    [
                        html.H3("MPs"),
                        dbc.Label("Select Parliament"),
                        dcc.Dropdown(
                            id="parliament-select",
                            options=parliamentarians.representedparliaments.sort_values(
                                ascending=False
                            ).unique(),
                        ),
                        dbc.Label("Select an MP"),
                        dcc.Dropdown(
                            id="member-select",
                            options=parliamentarians.member.sort_values().unique(),
                            multi=True,
                            value=47,
                        ),
                        # add dbc select for state
                        dbc.Label("Select State"),
                        dcc.Dropdown(
                            id="state-select",
                            options=parliamentarians.state.sort_values().unique(),
                        ),
                        # add dbc select for school sector
                        dbc.Label("Select School Sector"),
                        dcc.Dropdown(
                            id="school-sector-select",
                            options=gdf["school_sector"]
                            .dropna()
                            .sort_values()
                            .unique(),
                        ),
                        # add dbc select for party
                        dbc.Label("Select Political Parties"),
                        dcc.Dropdown(
                            id="party-select",
                            options=gdf["party"].dropna().sort_values().unique(),
                            multi=True,
                        ),
                        # add dbc select for party
                        dbc.Label("Select Electorate or Division"),
                        dcc.Dropdown(
                            id="electorate-select",
                            options=gdf["district"].dropna().sort_values().unique(),
                            multi=True,
                        ),
                    ],
                    id="cross-filter-options",
                    width=3,
                ),
                dbc.Col(
                    html.Div(
                        [
                            html.H3("Schools"),
                            dcc.Graph(id="map", figure=fig),
                        ]
                    ),
                    id="map-column",
                ),
                dbc.Col(
                    html.Div(
                        [
                            html.H3("Theme: Age vs Gender"),
                            dbc.Row(
                                dcc.Graph(figure=histogram),
                            ),
                        ]
                    ),
                    id="right-column",
                    width=3,
                ),
            ]
        ),
        html.Hr(),
        dbc.Row(
            [
                dbc.Col(
                    html.Div(
                        table,
                        style={"overflowY": "scroll"},
                    ),
                    width={"size": 10, "offset": 1},
                    id="table-container",
                )
            ]
        ),
        html.Hr(),
        dbc.Row(
            [
                # add pie chart for school sector
                dbc.Col(
                    dcc.Graph(
                        figure=pie_fig,
                        id="pie-chart-school-sector",
                    ),
                    width={"size": 3, "offset": 1},
                ),
                dbc.Col(
                    dcc.Graph(
                        figure=pie_gender_fig,
                        id="pie-chart-gender",
                    ),
                    width={"size": 3},
                ),
                dbc.Col(
                    dcc.Graph(
                        figure=pie_party_fig,
                        id="pie-chart-party",
                    ),
                    width={"size": 3},
                ),
            ]
        )
        #
        # dcc.Graph(figure=px.histogram(parliamentarians_by_parl, x='party_abbrev', y='age', histfunc="avg",
        #                               facet_row="RepresentedParliaments",
        #                               color='Gender'))
    ],
    id="mainContainer",
)


@app.callback(
    Output("viewport-store", "data"),
    Input("map", "relayoutData"),
)
# fix this
def store_viewport(current_map):
    print(current_map)
    if current_map:
        mapbox_data = current_map.get("mapbox._derived", {})
        if mapbox_data:
            return mapbox_data.get("coordinates")


def create_bounding_box(center, zoom):
    lat, lon = center["lat"], center["lon"]
    lat_delta = 360 / (2**zoom) * 0.5
    lon_delta = 360 / (2**zoom) * 0.5 / math.cos(math.radians(lat))
    sw = (lat - lat_delta, lon - lon_delta)
    ne = (lat + lat_delta, lon + lon_delta)
    bbox = [
        [sw[0], sw[1]],
        [sw[0], ne[1]],
        [ne[0], ne[1]],
        [ne[0], sw[1]],
        [sw[0], sw[1]],
    ]
    return bbox


@app.callback(
    Output("bounds-store", "data"),
    Input("map", "figure"),
)
def store_bounds(figure):
    print(figure)
    if not figure:
        raise PreventUpdate
    if "layout" not in figure:
        raise PreventUpdate
    center = figure["layout"]["mapbox"].get("center")
    zoom = figure["layout"]["mapbox"].get("zoom")
    if not center or not zoom:
        raise PreventUpdate
    return {"center": center, "zoom": zoom}


@app.callback(
    Output("table", "data"),
    [
        Input("viewport-store", "data"),
        Input("bounds-store", "data"),
        Input("parliament-select", "value"),
        Input("state-select", "value"),
        Input("school-sector-select", "value"),
        Input("party-select", "value"),
        Input("electorate-select", "value"),
    ],
)
def update_table(
    bbox,
    bounds,
    parliament,
    state,
    school_sector,
    party,
    electorate,
):
    # Extract the viewport bounds
    # create a shapely bbox based on the viewport bounds
    # We need to use both bounds and bbox here. The bounds are used to filter the data
    # and the bbox is used to trigger the callback

    if bbox or bounds:
        bbox = Polygon(bbox)
        # Filter the data based on the viewport bounds
        if not bbox.is_empty:
            mask = gdf.geometry.intersects(bbox)
            filtered_df = gdf[mask]
        else:
            filtered_df = gdf.copy()
        filtered_df = generate_geo_map(
            filtered_df,
            parliament,
            party=party,
            state=state,
            electorate=electorate,
            school_sector=school_sector,
        )

        # Convert the filtered data to a dictionary for use in the data table
        filtered_data = filtered_df[map_cols].to_dict("records")
        return filtered_data
    return gdf[map_cols].to_dict("records")


@app.callback(
    Output("map", "figure"),
    [
        Input("parliament-select", "value"),
        Input("state-select", "value"),
        Input("school-sector-select", "value"),
        Input("party-select", "value"),
        Input("electorate-select", "value"),
    ],
    [State("map", "relayoutData")],
)
def update_geo_map(
    parliament_select, state_select, school_sector, party, electorate, main_graph_layout
):
    # generate geo map from state-select, procedure-select
    # add chamber and gender and party select
    filtered_data = generate_geo_map(
        gdf,
        parliament_select,
        party,
        state=state_select,
        school_sector=school_sector,
        electorate=electorate,
    )
    text = (
        filtered_data["member"]
        + "<br>"
        + "<b>"
        + filtered_data["name"]
        + "/<b> <br>"
        + ""
        + filtered_data["party_abbrv"]
    )
    trace = dict(
        type="scattermapbox",
        lat=filtered_data.geometry.y,
        lon=filtered_data.geometry.x,
        customdata=filtered_data[map_cols].to_dict("records"),
        color="school_sector",
        size="total_students",
        name=filtered_data.member,
        hoverinfo="text",
        hovertext=text,
        color_discrete_sequence=px.colors.qualitative.Dark24,
        mapbox_style="dark",
        hover_data=map_cols[1:],
    )
    # relayoutData is None by default, and {'autosize': True} without relayout action
    if main_graph_layout is not None:
        if (
            "mapbox.center" in main_graph_layout.keys()
            and "center" in layout["mapbox"]
            and "zoom" in layout["mapbox"]
        ):
            lon = float(main_graph_layout["mapbox.center"]["lon"])
            lat = float(main_graph_layout["mapbox.center"]["lat"])
            zoom = main_graph_layout["mapbox.zoom"]
            layout["mapbox"]["center"]["lon"] = lon
            layout["mapbox"]["center"]["lat"] = lat
            layout["mapbox"]["zoom"] = zoom
    if not filtered_data.empty:
        center_lat, center_lon, zoom = calculate_zoom(filtered_data, zoom)
        layout["mapbox"]["center"]["lon"] = center_lon
        layout["mapbox"]["center"]["lat"] = center_lat
        layout["mapbox"]["zoom"] = zoom
    return dict(data=[trace], layout=layout)


def calculate_zoom(filtered_data):
    minx, miny, maxx, maxy = filtered_data.total_bounds
    # Calculate the center point of the bounding box
    center_lat = (miny + maxy) / 2
    center_lon = (minx + maxx) / 2
    # Calculate the zoom level based on the range of the bounding box
    # max_bound = * 111
    # zoom = 11.5 - np.log(max_bound)
    max_range = max(abs(maxx - minx), abs(maxy - miny))
    # Determine the appropriate zoom level based on the range
    if max_range < 0.1:
        zoom = 13
    elif max_range < 0.2:
        zoom = 12
    elif max_range < 0.5:
        zoom = 11
    elif max_range < 1:
        zoom = 10
    elif max_range < 2:
        zoom = 9
    elif max_range < 5:
        zoom = 8
    elif max_range < 10:
        zoom = 7
    elif max_range < 20:
        zoom = 6
    else:
        zoom = 5
    return center_lat, center_lon, zoom


def generate_geo_map(
    geo_data,
    parliament=None,
    party=None,
    gender=None,
    electorate=None,
    school_sector=None,
    state=None,
):
    masks = None
    if parliament:
        masks = geo_data["mp_id"].isin(
            parliamentarians[
                parliamentarians.representedparliaments == parliament
            ].mp_id
        )
    if party:
        masks = (
            masks & geo_data["party"].isin(party)
            if masks is not None
            else geo_data["party"].isin(party)
        )
    if gender:
        masks = (
            masks & geo_data["gender"].isin(gender)
            if masks is not None
            else geo_data["gender"].isin(gender)
        )
    if electorate:
        masks = (
            masks & geo_data["district"].isin(electorate)
            if masks is not None
            else geo_data["district"].isin(electorate)
        )
    if school_sector:
        masks = (
            masks & geo_data["school_sector"] == school_sector
            if masks is not None
            else geo_data["district"] == school_sector
        )
    if state:
        masks = (
            masks & geo_data["stateabbrev"] == STATE_LOOKUP[state]
            if masks is not None
            else geo_data["stateabbrev"] == STATE_LOOKUP[state]
        )
    if masks is not None:
        geo_data = geo_data[masks]

    return geo_data


def generate_queries(gdf):
    data = {}
    member_grouped = gdf.groupby("member")["school_sector"].transform(
        lambda x: "Both"
        if "Government" in x.values
        and ("Independent" in x.values or "Catholic" in x.values)
        else "none"
        if "Government" not in x.values
        and ("Independent" not in x.values and "Catholic" not in x.values)
        else "Government"
        if "Government" in x.values
        else "Independent"
        if "Independent" in x.values
        else "Catholic"
    )
    data_school_sector = (
        member_grouped.drop_duplicates(["member", "school_sector"])
        .groupby(["party", "school_sector"])
        .size()
        .reset_index(name="count")
    )
    data["school_sector"] = data_school_sector


# Table of GDF parliamentarians
# We want to filter by parliament, party, gender, age, electorate, etc
# we want to display pie chart for school sector percentage.
# We could also add pages like so http://plotlychallengechurn-env.eba-jidvvwmp.us-east-2.elasticbeanstalk.com/churn

# fig.update_layout(
#     mapbox_style="white-bg",
#     mapbox_layers=[
#         {
#             "below": 'traces',
#             "sourcetype": "raster",
#             "sourceattribution": "United States Geological Survey",
#             "source": [
#                 "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
#             ]
#         },
# mapbox_bounds={"west": -180, "east": -50, "south": 20, "north": 90}
#       ])

# @callback(
#     Output('graph-content', 'figure'),
#     Input('dropdown-selection', 'value')
# )
# def update_graph(value):
#     dff = df[df.country==value]
#     return px.line(dff, x='year', y='pop')

if __name__ == "__main__":
    app.run_server(debug=True)
