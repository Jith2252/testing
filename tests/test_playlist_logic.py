from playlist_logic import (
    next_index,
    ordered_neighbor,
    playlist_order,
    previous_index,
)


def test_playlist_order_preserves_file_picker_order():
    files = ["third.mp4", "first.mp4", "second.mp4"]

    assert playlist_order(files) == files
    assert playlist_order(iter(files)) == files


def test_previous_and_next_are_bounded_without_wrapping():
    assert previous_index(0, 3) is None
    assert previous_index(2, 3) == 1
    assert next_index(0, 3) == 1
    assert next_index(2, 3) is None
    assert next_index(-1, 3) is None
    assert previous_index(3, 3) is None


def test_ordered_neighbor_matches_queue_navigation():
    files = ["one.mp4", "two.mp4", "three.mp4"]

    assert ordered_neighbor(files, 0, 1) == "two.mp4"
    assert ordered_neighbor(files, 1, -1) == "one.mp4"
    assert ordered_neighbor(files, 0, -1) is None
    assert ordered_neighbor(files, 2, 1) is None
    assert ordered_neighbor(files, 1, 0) is None
