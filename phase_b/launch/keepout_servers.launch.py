#!/usr/bin/env python3
"""Day-3 keepout 滤镜服务栈:filter_mask_server(发布掩码 OccupancyGrid)+
costmap_filter_info_server(发布 CostmapFilterInfo)+ 它俩的 lifecycle_manager(autostart)。

必须在主 nav2 栈激活 global_costmap【之前】起来并 active —— 否则 KeepoutFilter 等不到
/costmap_filter_info(latched)会导致代价地图激活失败。故 smoke 先起本 launch、等 active,再起主栈。

参数取自 params_file 的 filter_mask_server / costmap_filter_info_server 段(默认 nav2_params.yaml)。
"""
import os

from ament_index_python.packages import get_package_share_directory  # noqa: F401
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    params_file = LaunchConfiguration("params_file")

    declare_params = DeclareLaunchArgument(
        "params_file",
        default_value="/hostpb/nav2_params.yaml",
        description="含 filter_mask_server / costmap_filter_info_server 段的参数文件",
    )

    filter_mask_server = Node(
        package="nav2_map_server",
        executable="map_server",
        name="filter_mask_server",
        output="screen",
        parameters=[params_file, {"use_sim_time": True}],
    )

    costmap_filter_info_server = Node(
        package="nav2_map_server",
        executable="costmap_filter_info_server",
        name="costmap_filter_info_server",
        output="screen",
        parameters=[params_file, {"use_sim_time": True}],
    )

    lifecycle_manager = Node(
        package="nav2_lifecycle_manager",
        executable="lifecycle_manager",
        name="lifecycle_manager_costmap_filters",
        output="screen",
        parameters=[{
            "use_sim_time": True,
            "autostart": True,
            "node_names": ["filter_mask_server", "costmap_filter_info_server"],
        }],
    )

    return LaunchDescription([
        declare_params,
        filter_mask_server,
        costmap_filter_info_server,
        lifecycle_manager,
    ])
