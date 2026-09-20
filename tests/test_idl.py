from pathlib import Path
from evergreen_holder.idl import parse_field_map, load_field_map

SAMPLE = """<IDL xmlns='http://opensrf.org/spec/IDL/base/v1'>
  <class id='aou' controller='open-ils.cstore'>
    <fields oils_persist:primary='id' xmlns:oils_persist='x'>
      <field name='children' virtual='true'/>
      <field name='id'/>
      <field name='name'/>
    </fields>
  </class>
  <class id='mvr'>
    <fields>
      <field name='title'/>
      <field name='author'/>
    </fields>
  </class>
</IDL>"""


def test_parse_field_map_keeps_field_order():
    m = parse_field_map(SAMPLE)
    assert m["aou"] == ["children", "id", "name"]
    assert m["mvr"] == ["title", "author"]


def test_load_field_map_reads_file(tmp_path: Path):
    p = tmp_path / "fm_IDL.xml"
    p.write_text(SAMPLE)
    assert load_field_map(p)["mvr"] == ["title", "author"]
