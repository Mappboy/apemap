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
from datetime import date

import dash_bootstrap_components as dbc
import geopandas as gpd
import pandas as pd
import plotly.express as px
from dash import Dash, html, dcc, dash_table, Input, Output
from dash.exceptions import PreventUpdate
from dotenv import load_dotenv
from shapely import Polygon
from sqlalchemy import create_engine

BGCOLOR = "#222"

TRANSPARENT = "rgba(0,0,0,0)"

load_dotenv("../.env")

engine = create_engine(
    f"postgresql+psycopg://{os.environ.get('DATABASE_USERNAME')}:{os.environ.get('DATABASE_PASSWORD')}@localhost:5432/{os.environ.get('DATABASE_NAME')}"
)

today = date.today()

parliamentarians = pd.read_sql(
    """
SELECT row_number() over (), members_aph.* FROM members_aph
    ORDER BY "GivenName" asc
""",
    engine,
)

parliamentarians = parliamentarians.explode("RepresentedParliaments")
parliamentarians["politician_type"] = parliamentarians["is_representative"].apply(
    lambda x: "Member" if x else "Senator"
)
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
#     ["member", "politician_type", "age", "dob", "district", "party_abbrev", "RepresentedParliaments", "Gender",
#      "State", "Image", "wiki_link"]]
parliamentarians.columns = parliamentarians.columns.str.lower().str.replace(" ", "_")

gdf = gpd.read_postgis(
    'SELECT m.*, "total enrolments" as total_students '
    "FROM minister_secondary_school_education_47 m "
    'LEFT JOIN acara_school_profile_2022 a on m.acara_id = a."acara sml id"::int ORDER BY member asc, "school name"',
    engine,
    geom_col="geom",
)

gdf = gdf.convert_dtypes()
# fill missing students with min 50 students
gdf["total_students"] = (
    gdf["total_students"].fillna("50").apply(lambda x: "50" if not x else x)
)
gdf["total_students"] = gdf["total_students"].astype(int)
gdf.columns = gdf.columns.str.lower().str.replace(" ", "_")


def get_chamber_counts(df):
    return df.groupby(["politician_type"]).size().reset_index(name="count")


def get_party_counts(df):
    return df.groupby(["party_abbrv"]).size().reset_index(name="count")


def get_gender_counts(df):
    return df.groupby(["gender"]).size().reset_index(name="count")


# implement map
# https://dash.plotly.com/holoviews + datashader + Rapids
# Scattergeo
layout = dict(
    autosize=True,
    automargin=True,
    margin=dict(l=30, r=30, b=20, t=40),
    hovermode="closest",
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
    legend=dict(font=dict(size=10), orientation="h"),
    title="Satellite Overview",
    mapbox=dict(
        style="dark",
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
hovertemplate = (
    "<b>Lat:</b> %{lat:.2f}<br><b>Lon:</b> %{lon:.2f}<br><br>"
    + "<b>Values:</b> %{text} <extra>%{fullData.member}</extra>"
)

table_cols = [
    "mp_id",
    "member",
    "school_name",
    "school_sector",
    "total_students",
    "party",
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

px.set_mapbox_access_token(os.environ.get("MAPBOX_TOKEN"))
fig = px.scatter_mapbox(
    gdf,
    lat=gdf.geometry.y,
    lon=gdf.geometry.x,
    color="school_sector",
    size="total_students",
    color_discrete_sequence=px.colors.qualitative.Dark24,
    mapbox_style="dark",
    hover_data=table_cols[1:],
    zoom=4,
    height=500,
)
fig.update_layout(
    height=800,
    margin={"r": 0, "t": 0, "l": 0, "b": 0},
    plot_bgcolor=TRANSPARENT,
    paper_bgcolor=TRANSPARENT,
    legend=dict(
        bgcolor=BGCOLOR,
        orientation="h",
        yanchor="bottom",
        y=1,
        xanchor="center",
        x=1,
        font=dict(family="Roboto", size=12, color="lightgrey"),
    ),
)
fig.update_traces(cluster=dict(enabled=True))
# set hover_template to show all values in the cluster

# Todo add table callback to zoom to location
table = dash_table.DataTable(
    id="table",
    data=gdf[table_cols].to_dict("records"),
    page_size=25,
    columns=[
        {"name": i, "id": i, "deletable": True, "selectable": True} for i in gdf.columns
    ],
    style_header={"backgroundColor": "rgb(30, 30, 30)", "color": "white"},
    style_data={"backgroundColor": "rgb(50, 50, 50)", "color": "white"},
    style_data_conditional=[
        {"if": {"row_index": "odd"}, "backgroundColor": "rgb(50, 50, 50)"},
        {
            "if": {"state": "active"},
            "backgroundColor": "rgba(0, 116, 217, 0.3)",
            "border": "1px solid rgba(0, 116, 217, 0.3)",
        },
        {"if": {"column_id": "member"}, "textAlign": "left"},
        {"if": {"column_id": "age"}, "textAlign": "right"},
        {"if": {"column_id": "gender"}, "textAlign": "center"},
        {
            "if": {"filter_query": "{age} > 30"},
            "backgroundColor": "rgba(255, 0, 0, 0.5)",
        },
        {
            "if": {"filter_query": '{gender} contains "Male"'},
            "backgroundColor": "rgba(0, 255, 0, 0.5)",
        },
    ],
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
    .rename({"mean": "age"}, axis=1),
    x="party_abbrev",
    y="age",
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
                            html.H3("Information"),
                            dbc.Row(
                                dcc.Markdown(
                                    """# Add some information based on current  filter here.
                    """
                                )
                            ),
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
    center = figure["layout"]["mapbox"]["center"]
    zoom = figure["layout"]["mapbox"]["zoom"]
    return {"center": center, "zoom": zoom}


@app.callback(
    Output("table", "data"),
    [
        Input("viewport-store", "data"),
        Input("bounds-store", "data"),
    ],
)
def update_table(bbox, bounds):
    # Extract the viewport bounds
    # create a shapely bbox based on the viewport bounds
    # We need to use both bounds and bbox here. The bounds are used to filter the data
    # and the bbox is used to trigger the callback
    if not bbox:
        raise PreventUpdate
    if bbox and bounds:
        bbox = Polygon(bbox)
        # Filter the data based on the viewport bounds
        filtered_df = gdf[gdf.geometry.intersects(bbox)]

        # Convert the filtered data to a dictionary for use in the data table
        filtered_data = filtered_df[table_cols].to_dict("records")
        return filtered_data
    return gdf[table_cols].to_dict("records")


def generate_geo_map(
    geo_data, parliament, party, gender, electorate, school_sector, state
):
    masks = None
    if parliament:
        masks = geo_data.parliament.isin(parliament)
    if party:
        masks = (
            masks & geo_data.party.isin(party)
            if masks is not None
            else geo_data.party.isin(party)
        )
    if gender:
        masks = (
            masks & geo_data.gender.isin(gender)
            if masks is not None
            else geo_data.gender.isin(gender)
        )
    if electorate:
        masks = (
            masks & geo_data.distict.isin(electorate)
            if masks is not None
            else geo_data.distict.isin(electorate)
        )
    if school_sector:
        masks = (
            masks & geo_data.distict.isin(school_sector)
            if masks is not None
            else geo_data.distict.isin(school_sector)
        )
    if state:
        masks = (
            masks & geo_data.state.isin(state)
            if masks is not None
            else geo_data.state.isin(state)
        )
    if masks:
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
