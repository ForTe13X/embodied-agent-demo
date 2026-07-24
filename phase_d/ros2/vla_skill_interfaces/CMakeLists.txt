cmake_minimum_required(VERSION 3.8)
project(vla_skill_interfaces)

find_package(ament_cmake REQUIRED)
find_package(action_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "action/ExecuteVLASkill.action"
  DEPENDENCIES action_msgs
)

ament_package()
