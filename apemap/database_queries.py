"""Just some handy queries for the database."""
import pandas as pd


def get_school_like_name(name, engine):
    """Get all schools in a postcode."""
    return pd.read_sql(
        f"""
        SELECT * FROM schools
        WHERE name LIKE '%{name}%'
        """,
        con=engine,
    )


def get_members_school_id(school_id, engine):
    """Get all members who attended a school."""
    return pd.read_sql(
        f"""
        SELECT * FROM members
        JOIN member_education me on members.id = me.member_id
        JOIN education e on e.id = me.education_id
        WHERE e.id = {school_id}
        """,
        con=engine,
    )
